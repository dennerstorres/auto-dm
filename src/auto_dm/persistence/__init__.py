"""Save / load :class:`GameState` to and from JSON files."""
from auto_dm.persistence.saves import (
    SaveError,
    SaveMetadata,
    SaveNotFoundError,
    SchemaMismatchError,
    default_saves_dir,
    delete_save,
    list_saves,
    load_metadata,
    load_state,
    save_exists,
    save_state,
    slugify,
)

__all__ = [
    "SaveError",
    "SaveMetadata",
    "SaveNotFoundError",
    "SchemaMismatchError",
    "default_saves_dir",
    "delete_save",
    "list_saves",
    "load_metadata",
    "load_state",
    "save_exists",
    "save_state",
    "slugify",
]