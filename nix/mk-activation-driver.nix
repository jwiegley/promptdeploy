{
  pkgs,
  package,
  source,
  stateDir,
  targets ? [ ],
  exactItems ? [
    "mcp:anvil"
    "mcp:anvil-tools"
    "skill:anvil"
  ],
  lockWaitSeconds ? 60,
  transactionTimeoutSeconds ? 300,
  logMaxBytes ? 1048576,
}:

let
  inherit (pkgs) lib;
  targetArgs = lib.concatMapStringsSep " " (target: "--target ${lib.escapeShellArg target}") targets;
  itemArgs = lib.concatMapStringsSep " " (item: "--only-item ${lib.escapeShellArg item}") exactItems;
  transaction = pkgs.writeShellScript "promptdeploy-home-transaction" ''
    set -euo pipefail
    unset PROMPTDEPLOY_HOST
    cd -- ${lib.escapeShellArg (toString source)}
    ${lib.getExe package} deploy --local-only --force --quiet ${targetArgs}
    ${lib.getExe package} verify --local-only ${targetArgs} ${itemArgs}
  '';
  psutilPath = "${pkgs.python3Packages.psutil}/${pkgs.python3.sitePackages}";
  transactionRunner = pkgs.writeText "promptdeploy-home-transaction-runner.py" ''
    import signal
    import subprocess
    import sys
    import time

    sys.path.insert(0, ${builtins.toJSON psutilPath})
    import psutil


    timeout_seconds = float(sys.argv[1])
    grace_seconds = float(sys.argv[2])
    command = sys.argv[3]


    def is_alive(process: psutil.Process) -> bool:
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False


    def expand_descendants(
        tracked: dict[int, psutil.Process],
    ) -> list[psutil.Process]:
        for candidate in list(tracked.values()):
            if not is_alive(candidate):
                continue
            try:
                descendants = candidate.children(recursive=True)
            except (psutil.AccessDenied, psutil.NoSuchProcess):
                continue
            for descendant in descendants:
                tracked.setdefault(descendant.pid, descendant)
        return [candidate for candidate in tracked.values() if is_alive(candidate)]


    def signal_process(
        process: psutil.Process,
        signal_number: signal.Signals,
    ) -> None:
        try:
            process.send_signal(signal_number)
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            pass


    def terminate_tree(
        process: subprocess.Popen[bytes],
        signal_number: signal.Signals,
    ) -> None:
        try:
            root = psutil.Process(process.pid)
            tracked = {
                descendant.pid: descendant
                for descendant in root.children(recursive=True)
            }
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            tracked = {}

        for descendant in reversed(expand_descendants(tracked)):
            signal_process(descendant, signal_number)
        try:
            process.send_signal(signal_number)
        except ProcessLookupError:
            pass

        deadline = time.monotonic() + grace_seconds
        while time.monotonic() < deadline:
            descendants = expand_descendants(tracked)
            if process.poll() is not None and not descendants:
                return
            time.sleep(0.05)

        descendants = expand_descendants(tracked)
        for descendant in reversed(descendants):
            signal_process(descendant, signal.SIGKILL)
        if process.poll() is None:
            process.kill()
        psutil.wait_procs(descendants, timeout=grace_seconds)
        try:
            process.wait(timeout=grace_seconds)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


    process = subprocess.Popen([command])


    def forward_signal(signal_number: int, _frame: object) -> None:
        terminate_tree(process, signal.Signals(signal_number))
        raise SystemExit(128 + signal_number)


    for forwarded_signal in (signal.SIGHUP, signal.SIGINT, signal.SIGTERM):
        signal.signal(forwarded_signal, forward_signal)

    try:
        return_code = process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        terminate_tree(process, signal.SIGTERM)
        raise SystemExit(124)

    if return_code < 0:
        raise SystemExit(128 - return_code)
    raise SystemExit(return_code)
  '';
in
pkgs.writeShellApplication {
  name = "promptdeploy-home-activation";
  runtimeInputs = [
    pkgs.coreutils
    pkgs.gnused
    pkgs.python3
  ];
  text = ''
    umask 077

    state_dir=${lib.escapeShellArg stateDir}
    lock_dir="$state_dir/activation.lock"
    owner_file="$lock_dir/owner"
    log_path="$state_dir/deploy.log"
    acquired=0
    owner_tmp=
    raw_log=
    bounded_log=
    token=

    cleanup() {
      status=$?
      trap - EXIT HUP INT TERM

      if [ "$acquired" -eq 1 ]; then
        if [ -f "$owner_file" ] && [ ! -L "$owner_file" ]; then
          owner_token=$(${pkgs.gnused}/bin/sed -n '1s/^token=//p' "$owner_file")
          if [ "$owner_token" = "$token" ]; then
            ${pkgs.coreutils}/bin/rm -f -- "$owner_file" || true
            ${pkgs.coreutils}/bin/rmdir -- "$lock_dir" || true
          else
            echo "promptdeploy activation lock ownership changed; lock retained" >&2
            if [ "$status" -eq 0 ]; then
              status=1
            fi
          fi
        elif [ -e "$owner_file" ] || [ -L "$owner_file" ]; then
          echo "promptdeploy activation lock owner is unsafe; lock retained" >&2
          if [ "$status" -eq 0 ]; then
            status=1
          fi
        fi
      fi

      if [ -n "$owner_tmp" ]; then
        ${pkgs.coreutils}/bin/rm -f -- "$owner_tmp" || true
      fi
      if [ -n "$raw_log" ]; then
        ${pkgs.coreutils}/bin/rm -f -- "$raw_log" || true
      fi
      if [ -n "$bounded_log" ]; then
        ${pkgs.coreutils}/bin/rm -f -- "$bounded_log" || true
      fi
      exit "$status"
    }
    trap cleanup EXIT
    trap 'exit 129' HUP
    trap 'exit 130' INT
    trap 'exit 143' TERM

    if [ -L "$state_dir" ]; then
      echo "promptdeploy activation state directory is unsafe" >&2
      exit 1
    fi
    ${pkgs.coreutils}/bin/mkdir -p -- "$state_dir"
    if [ ! -d "$state_dir" ] || [ ! -O "$state_dir" ]; then
      echo "promptdeploy activation state directory is unsafe" >&2
      exit 1
    fi
    ${pkgs.coreutils}/bin/chmod 0700 "$state_dir"

    remaining=${toString lockWaitSeconds}
    while ! ${pkgs.coreutils}/bin/mkdir -m 0700 -- "$lock_dir" 2>/dev/null; do
      if [ "$remaining" -le 0 ]; then
        echo "promptdeploy activation failed to acquire its deployment lock" >&2
        exit 1
      fi
      ${pkgs.coreutils}/bin/sleep 1
      remaining=$((remaining - 1))
    done
    acquired=1

    token=$(
      ${pkgs.coreutils}/bin/od -An -N16 -tx1 /dev/urandom |
        ${pkgs.coreutils}/bin/tr -d ' \n'
    )
    if [ "''${#token}" -ne 32 ]; then
      echo "promptdeploy activation could not create a lock token" >&2
      exit 1
    fi
    owner_tmp="$lock_dir/.owner.$token"
    {
      printf 'token=%s\n' "$token"
      printf 'host=%s\n' "$(${pkgs.coreutils}/bin/uname -n)"
      printf 'pid=%s\n' "$$"
      printf 'started=%s\n' "$(${pkgs.coreutils}/bin/date -u +%Y-%m-%dT%H:%M:%SZ)"
    } >"$owner_tmp"
    ${pkgs.coreutils}/bin/chmod 0600 "$owner_tmp"
    ${pkgs.coreutils}/bin/mv -- "$owner_tmp" "$owner_file"
    owner_tmp=

    raw_log=$(${pkgs.coreutils}/bin/mktemp "$state_dir/.deploy.raw.XXXXXX")
    bounded_log=$(${pkgs.coreutils}/bin/mktemp "$state_dir/.deploy.bounded.XXXXXX")
    ${pkgs.coreutils}/bin/chmod 0600 "$raw_log" "$bounded_log"

    set +e
    ${pkgs.python3}/bin/python3 -I -S \
      ${transactionRunner} \
      ${toString transactionTimeoutSeconds} \
      5 \
      ${transaction} >"$raw_log" 2>&1
    transaction_status=$?
    set -e

    ${pkgs.coreutils}/bin/tail -c ${toString logMaxBytes} "$raw_log" >"$bounded_log"
    ${pkgs.coreutils}/bin/chmod 0600 "$bounded_log"
    ${pkgs.coreutils}/bin/mv -f -- "$bounded_log" "$log_path"
    bounded_log=
    ${pkgs.coreutils}/bin/rm -f -- "$raw_log"
    raw_log=

    if [ "$transaction_status" -ne 0 ]; then
      echo "promptdeploy activation failed; see $log_path" >&2
      exit "$transaction_status"
    fi
  '';
}
