from enum import Enum
from pydantic import BaseModel


class SensitivityLevel(str, Enum):
    # Safe for normal analytical use.
    SAFE = "safe"
    # Doesnt directly identify a person, but in combination does
    QUASI_IDENTIFIER = "quasi_identifier"
    # Directly identifies or can be used to contact a specific person.
    DIRECT_PII = "direct_pii"




class ColumnPolicy(BaseModel):
    sensitivity: SensitivityLevel
    description: str = ""