{
  pkgs,
}:

let
  fixtureSource = pkgs.runCommand "promptdeploy-activation-source" { } ''
    mkdir -p "$out"
    touch "$out/deploy.yaml"
  '';

  fakePromptdeploy = pkgs.writeShellApplication {
    name = "promptdeploy";
    runtimeInputs = [ pkgs.coreutils ];
    text = ''
      set -euo pipefail

      command=''${1:?missing command}
      shift
      trace=''${PROMPTDEPLOY_TEST_TRACE:?missing trace path}
      tag=''${PROMPTDEPLOY_TEST_TAG:-case}
      line="$tag|$command"
      for argument in "$@"; do
        line="$line|$argument"
      done
      line="$line|host=''${PROMPTDEPLOY_HOST-unset}"
      printf '%s\n' "$line" >>"$trace"

      if [ -n "''${PROMPTDEPLOY_TEST_EVENTS:-}" ]; then
        printf '%s:%s:start\n' "$tag" "$command" >>"$PROMPTDEPLOY_TEST_EVENTS"
      fi

      case "''${PROMPTDEPLOY_TEST_MODE:-success}:$command" in
        deploy-fail:deploy)
          printf '%s\n' "DEPLOY_SECRET_MUST_STAY_PRIVATE" >&2
          exit 41
          ;;
        verify-fail:verify)
          printf '%s\n' "VERIFY_SECRET_MUST_STAY_PRIVATE" >&2
          exit 42
          ;;
        timeout:deploy)
          printf '%s\n' "$$" >"''${PROMPTDEPLOY_TEST_PID_FILE:?missing pid path}"
          trap "" TERM
          exec sleep 3600
          ;;
        owner-swap:deploy)
          printf '%s\n' "token=foreign-owner" >"''${PROMPTDEPLOY_TEST_STATE:?missing state path}/activation.lock/owner"
          ;;
        flood:verify)
          head -c 4096 /dev/zero | tr '\0' X
          printf '%s\n' "TAIL_MARKER"
          ;;
        serialize:deploy)
          sleep 1
          ;;
      esac

      if [ -n "''${PROMPTDEPLOY_TEST_EVENTS:-}" ]; then
        printf '%s:%s:end\n' "$tag" "$command" >>"$PROMPTDEPLOY_TEST_EVENTS"
      fi
      printf 'fake %s output\n' "$command"
    '';
  };

  rootSuffix = builtins.substring 0 16 (builtins.hashString "sha256" (toString fakePromptdeploy));
  stateRoot = "/tmp/promptdeploy-hm-activation-${rootSuffix}";

  mkDriver =
    name: overrides:
    pkgs.callPackage ./mk-activation-driver.nix (
      {
        package = fakePromptdeploy;
        source = fixtureSource;
        stateDir = "${stateRoot}/${name}";
        lockWaitSeconds = 4;
        transactionTimeoutSeconds = 10;
        logMaxBytes = 1024;
      }
      // overrides
    );

  exactDriver = mkDriver "exact" {
    targets = [
      "claude-hera"
      "codex-local"
    ];
  };
  deployFailureDriver = mkDriver "deploy-failure" { };
  verifyFailureDriver = mkDriver "verify-failure" { };
  timeoutDriver = mkDriver "timeout" {
    transactionTimeoutSeconds = 1;
  };
  lockedDriver = mkDriver "locked" {
    lockWaitSeconds = 0;
  };
  ownerSwapDriver = mkDriver "owner-swap" { };
  floodDriver = mkDriver "flood" {
    logMaxBytes = 64;
  };
  serialDriver = mkDriver "serial" { };
  symlinkDriver = mkDriver "symlink" { };
in
pkgs.runCommand "promptdeploy-hm-activation-test"
  {
    nativeBuildInputs = [
      pkgs.coreutils
      pkgs.gnugrep
    ];
  }
  ''
    set -euo pipefail

    state_root=${pkgs.lib.escapeShellArg stateRoot}
    rm -rf -- "$state_root"
    mkdir -p -- "$state_root"
    cleanup() {
      rm -rf -- "$state_root"
    }
    trap cleanup EXIT

    exact_trace="$TMPDIR/exact.trace"
    PROMPTDEPLOY_TEST_TRACE="$exact_trace" \
      PROMPTDEPLOY_TEST_TAG=exact \
      PROMPTDEPLOY_HOST=must-be-unset \
      ${exactDriver}/bin/promptdeploy-home-activation
    printf '%s\n' \
      'exact|deploy|--local-only|--force|--quiet|--target|claude-hera|--target|codex-local|--only-item|mcp:anvil|--only-item|mcp:anvil-tools|--only-item|skill:anvil|host=unset' \
      'exact|verify|--local-only|--target|claude-hera|--target|codex-local|--only-item|mcp:anvil|--only-item|mcp:anvil-tools|--only-item|skill:anvil|host=unset' \
      >"$TMPDIR/exact.expected"
    cmp "$TMPDIR/exact.expected" "$exact_trace"
    test ! -e "$state_root/exact/activation.lock"
    test "$(stat -c %a "$state_root/exact")" = 700
    test "$(stat -c %a "$state_root/exact/deploy.log")" = 600

    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/deploy-failure.trace" \
      PROMPTDEPLOY_TEST_MODE=deploy-fail \
      ${deployFailureDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/deploy-failure.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 41
    grep -Fqx 'case|deploy|--local-only|--force|--quiet|--only-item|mcp:anvil|--only-item|mcp:anvil-tools|--only-item|skill:anvil|host=unset' "$TMPDIR/deploy-failure.trace"
    ! grep -Fq 'verify' "$TMPDIR/deploy-failure.trace"
    grep -Fq 'DEPLOY_SECRET_MUST_STAY_PRIVATE' "$state_root/deploy-failure/deploy.log"
    ! grep -Fq 'DEPLOY_SECRET_MUST_STAY_PRIVATE' "$TMPDIR/deploy-failure.console"
    grep -Fq 'promptdeploy activation failed; see ' "$TMPDIR/deploy-failure.console"
    test "$(stat -c %a "$state_root/deploy-failure/deploy.log")" = 600
    test ! -e "$state_root/deploy-failure/activation.lock"

    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/verify-failure.trace" \
      PROMPTDEPLOY_TEST_MODE=verify-fail \
      ${verifyFailureDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/verify-failure.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 42
    test "$(wc -l <"$TMPDIR/verify-failure.trace")" -eq 2
    grep -Fq 'VERIFY_SECRET_MUST_STAY_PRIVATE' "$state_root/verify-failure/deploy.log"
    ! grep -Fq 'VERIFY_SECRET_MUST_STAY_PRIVATE' "$TMPDIR/verify-failure.console"
    test ! -e "$state_root/verify-failure/activation.lock"

    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/timeout.trace" \
      PROMPTDEPLOY_TEST_MODE=timeout \
      PROMPTDEPLOY_TEST_PID_FILE="$TMPDIR/timeout.pid" \
      ${timeoutDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/timeout.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 124
    test -s "$TMPDIR/timeout.pid"
    timed_out_pid=$(cat "$TMPDIR/timeout.pid")
    child_alive=1
    for _attempt in $(seq 1 20); do
      if ! kill -0 "$timed_out_pid" 2>/dev/null; then
        child_alive=0
        break
      fi
      sleep 0.1
    done
    test "$child_alive" -eq 0
    test ! -e "$state_root/timeout/activation.lock"

    mkdir -m 0700 -p "$state_root/locked/activation.lock"
    printf '%s\n' 'token=preexisting-owner' >"$state_root/locked/activation.lock/owner"
    chmod 0600 "$state_root/locked/activation.lock/owner"
    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/locked.trace" \
      ${lockedDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/locked.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 1
    grep -Fqx 'token=preexisting-owner' "$state_root/locked/activation.lock/owner"
    test ! -e "$TMPDIR/locked.trace"
    grep -Fq 'failed to acquire its deployment lock' "$TMPDIR/locked.console"
    rm -rf -- "$state_root/locked/activation.lock"

    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/owner-swap.trace" \
      PROMPTDEPLOY_TEST_MODE=owner-swap \
      PROMPTDEPLOY_TEST_STATE="$state_root/owner-swap" \
      ${ownerSwapDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/owner-swap.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 1
    grep -Fqx 'token=foreign-owner' "$state_root/owner-swap/activation.lock/owner"
    grep -Fq 'lock ownership changed; lock retained' "$TMPDIR/owner-swap.console"
    rm -rf -- "$state_root/owner-swap/activation.lock"

    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/flood.trace" \
      PROMPTDEPLOY_TEST_MODE=flood \
      ${floodDriver}/bin/promptdeploy-home-activation
    test "$(wc -c <"$state_root/flood/deploy.log")" -le 64
    grep -Fq 'TAIL_MARKER' "$state_root/flood/deploy.log"
    test "$(stat -c %a "$state_root/flood/deploy.log")" = 600

    serial_trace="$TMPDIR/serial.trace"
    serial_events="$TMPDIR/serial.events"
    PROMPTDEPLOY_TEST_TRACE="$serial_trace" \
      PROMPTDEPLOY_TEST_EVENTS="$serial_events" \
      PROMPTDEPLOY_TEST_MODE=serialize \
      PROMPTDEPLOY_TEST_TAG=A \
      ${serialDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/serial-a.console" 2>&1 &
    first=$!
    for _attempt in $(seq 1 100); do
      if grep -Fqx 'A:deploy:start' "$serial_events" 2>/dev/null; then
        break
      fi
      sleep 0.1
    done
    grep -Fqx 'A:deploy:start' "$serial_events"
    PROMPTDEPLOY_TEST_TRACE="$serial_trace" \
      PROMPTDEPLOY_TEST_EVENTS="$serial_events" \
      PROMPTDEPLOY_TEST_MODE=serialize \
      PROMPTDEPLOY_TEST_TAG=B \
      ${serialDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/serial-b.console" 2>&1 &
    second=$!
    wait "$first"
    wait "$second"
    printf '%s\n' \
      'A:deploy:start' \
      'A:deploy:end' \
      'A:verify:start' \
      'A:verify:end' \
      'B:deploy:start' \
      'B:deploy:end' \
      'B:verify:start' \
      'B:verify:end' \
      >"$TMPDIR/serial.expected"
    cmp "$TMPDIR/serial.expected" "$serial_events"
    test ! -e "$state_root/serial/activation.lock"

    mkdir -p "$state_root/symlink-target"
    ln -s "$state_root/symlink-target" "$state_root/symlink"
    set +e
    PROMPTDEPLOY_TEST_TRACE="$TMPDIR/symlink.trace" \
      ${symlinkDriver}/bin/promptdeploy-home-activation \
      >"$TMPDIR/symlink.console" 2>&1
    status=$?
    set -e
    test "$status" -eq 1
    test ! -e "$TMPDIR/symlink.trace"
    test ! -e "$state_root/symlink-target/deploy.log"
    grep -Fq 'state directory is unsafe' "$TMPDIR/symlink.console"

    trap - EXIT
    cleanup
    touch "$out"
  ''
