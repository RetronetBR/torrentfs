from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass
class _Node:
    name: str
    is_dir: bool
    children: Dict[str, "_Node"] = field(default_factory=dict)
    file_index: Optional[int] = None
    size: int = 0


def _normalize(path: str) -> str:
    if not path:
        return ""
    return path.strip("/")


class PathIndex:
    """
    Árvores leves para mapear paths do torrent em O(parts) para list/stat.
    Mantém apenas metadados mínimos (tipo, size, file_index).
    """

    def __init__(self) -> None:
        self.root = _Node(name="", is_dir=True)

    def add_file(self, path: str, file_index: int, size: int) -> None:
        """
        Registra um arquivo no índice. Diretórios são criados sob demanda.
        """
        path = _normalize(path)
        if not path:
            raise ValueError("path vazio não é permitido")

        parts = path.split("/")
        cur = self.root
        for part in parts[:-1]:
            cur = cur.children.setdefault(part, _Node(name=part, is_dir=True))

        leaf_name = parts[-1]
        leaf = cur.children.get(leaf_name)
        if leaf is None:
            leaf = _Node(name=leaf_name, is_dir=False)
            cur.children[leaf_name] = leaf
        leaf.is_dir = False
        leaf.file_index = int(file_index)
        leaf.size = int(size)

    def _walk(self, path: str) -> _Node:
        """
        Navega até o node do path ou lança FileNotFoundError.
        """
        path = _normalize(path)
        if not path:
            return self.root

        cur = self.root
        for part in path.split("/"):
            nxt = cur.children.get(part)
            if nxt is None:
                raise FileNotFoundError(path)
            cur = nxt
        return cur

    def list_dir(self, path: str = "") -> List[dict]:
        node = self._walk(path)
        if not node.is_dir:
            raise NotADirectoryError(path)

        entries = []
        for name, child in sorted(node.children.items(), key=lambda kv: kv[0]):
            entries.append(
                {
                    "name": name,
                    "type": "dir" if child.is_dir else "file",
                    "size": 0 if child.is_dir else child.size,
                }
            )
        return entries

    def stat(self, path: str) -> dict:
        node = self._walk(path)
        if node.is_dir:
            return {"type": "dir", "size": 0}
        return {
            "type": "file",
            "size": node.size,
            "file_index": node.file_index,
        }
