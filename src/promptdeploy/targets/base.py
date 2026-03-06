from abc import ABC, abstractmethod
from pathlib import Path


class Target(ABC):
    @property
    @abstractmethod
    def id(self) -> str: ...

    @abstractmethod
    def exists(self) -> bool: ...

    @abstractmethod
    def deploy_agent(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_command(self, name: str, content: bytes) -> None: ...

    @abstractmethod
    def deploy_skill(self, name: str, source_dir: Path) -> None: ...

    @abstractmethod
    def deploy_mcp_server(self, name: str, config: dict) -> None: ...

    @abstractmethod
    def deploy_models(self, config: dict) -> None: ...

    @abstractmethod
    def deploy_hook(self, name: str, config: dict) -> None: ...

    @abstractmethod
    def remove_agent(self, name: str) -> None: ...

    @abstractmethod
    def remove_command(self, name: str) -> None: ...

    @abstractmethod
    def remove_skill(self, name: str) -> None: ...

    @abstractmethod
    def remove_mcp_server(self, name: str) -> None: ...

    @abstractmethod
    def remove_models(self) -> None: ...

    @abstractmethod
    def remove_hook(self, name: str) -> None: ...

    @abstractmethod
    def item_exists(self, item_type: str, name: str) -> bool:
        """Check if an item already exists at the deploy target path.

        Used to detect pre-existing items that were not deployed by
        promptdeploy and should not be overwritten or removed.
        """
        ...

    @abstractmethod
    def manifest_path(self) -> Path: ...
