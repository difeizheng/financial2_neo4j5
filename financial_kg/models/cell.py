from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class CellData:
    """Raw cell data extracted from Excel before graph construction."""
    sheet: str
    row: int
    col: str          # Excel column letter, e.g. "A", "BE"
    value: Any        # Computed value (data_only=True read)
    formula_raw: Optional[str]  # Raw formula string, e.g. "=SUM(F5:BE5)"
    data_type: str    # "number" | "string" | "date" | "bool" | "formula" | "empty"
    is_merged: bool = False
    merge_parent_id: Optional[str] = None  # id of the top-left cell in merge group

    @property
    def id(self) -> str:
        return f"{self.sheet}_{self.row}_{self.col}"


@dataclass
class Cell:
    """Cell node in the knowledge graph (Layer 1)."""
    id: str                        # "{sheet}_{row}_{col}"
    sheet: str
    row: int
    col: str
    value: Any
    formula_raw: Optional[str]
    data_type: str
    is_header: bool = False
    is_merged: bool = False
    merge_parent_id: Optional[str] = None
    # Populated after graph construction
    dependencies: list[str] = field(default_factory=list)   # cell ids this cell depends on
    dependents: list[str] = field(default_factory=list)     # cell ids that depend on this cell
    # Populated after indicator/table detection
    indicator_id: Optional[str] = None
    table_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "sheet": self.sheet,
            "row": self.row,
            "col": self.col,
            "value": self.value,
            "formula_raw": self.formula_raw,
            "data_type": self.data_type,
            "is_header": self.is_header,
            "is_merged": self.is_merged,
            "merge_parent_id": self.merge_parent_id,
            "dependencies": self.dependencies,
            "dependents": self.dependents,
            "indicator_id": self.indicator_id,
            "table_id": self.table_id,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Cell":
        return cls(**d)
