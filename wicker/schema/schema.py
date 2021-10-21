"""This file provides an API for defining an Avro-compatible schema in Python

Avro is a structured file format and schemas for Avro data are defined as JSON.
This module provides Python classes to encode these schemas as tree datastructure
which we can operate on using implementations of the DatasetSchemaVisitor class.

Avro specification: https://avro.apache.org/docs/current/spec.html
"""

from __future__ import annotations

import abc
import json
import re
from typing import Any, Dict, Generic, List, Optional, TypeVar

from wicker.core.errors import WickerSchemaException
from wicker.schema import codecs

__all__ = [
    "DatasetSchema",
    "DatasetSchemaVisitor",
    "SchemaField",
    "IntField",
    "LongField",
    "StringField",
    "BoolField",
    "FloatField",
    "DoubleField",
    "RecordField",
    "ArrayField",
    "ObjectField",
]

_T = TypeVar("_T")

PRIMARY_KEYS_TAG = "_primary_keys"


class SchemaField(abc.ABC):
    """Base class for all schema fields"""

    def __init__(
        self,
        name: str,
        description: str = "",
        required: bool = True,
        custom_field_tags: Dict[str, str] = {},
    ):
        """Create a new SchemaField

        :param name: name of the field
        :param description: description of the field, defaults to ""
        :param required: whether this field is required, defaults to True
        :param custom_field_tags: custom field tags to dump in schema, defaults to {}
        """
        # We reserve names starting with _ for internal use.
        if not re.match(r"^[a-zA-Z][0-9a-zA-Z_]*$", name):
            raise ValueError(
                "Schema name must contain only alphanumeric characters or underscores and cannot start with a number or"
                " underscore"
            )

        self.name: str = name
        self.description: str = description
        self.required = required
        self.custom_field_tags: Dict[str, str] = custom_field_tags

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, type(self)):
            return False
        elif self.name != other.name:
            return False
        elif self.description != other.description:
            return False
        elif self.required != other.required:
            return False
        return True

    @property
    def is_heavy_pointer(self) -> bool:
        """Whether this field is a heavy bytes field, in which case we should store a pointer and
        offload the contents to a separate file for more efficient columnar access

        This defaults to False for most fields, but individual SchemaField implementations should
        override this if they serialize to bytes and require offloading into a separate file.
        """
        return False

    @abc.abstractmethod
    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        raise NotImplementedError()


class IntField(SchemaField):
    """A field that stores a 32-bit int"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_int_field(self)


class LongField(SchemaField):
    """A field that stores a 64-bit long"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_long_field(self)


class StringField(SchemaField):
    """A field that stores a string"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_string_field(self)


class BoolField(SchemaField):
    """A field that stores a boolean"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_bool_field(self)


class FloatField(SchemaField):
    """A field that stores a 32-bit float"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_float_field(self)


class DoubleField(SchemaField):
    """A field that stores a 64-bit double"""

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_double_field(self)


class RecordField(SchemaField):
    """A field that contains nested fields"""

    def __init__(
        self,
        name: str,
        fields: List[SchemaField],
        description: str = "",
        required: bool = True,
        custom_field_tags: Dict[str, str] = {},
        top_level: bool = False,
    ):
        """Create a RecordField

        :param name: name of the field
        :param fields: list of nested SchemaFields
        :param description: description of the field, defaults to ""
        :param required: whether this field can be None, defaults to True
        :param custom_field_tags: custom field tags to dump in schema, defaults to {}
        :param top_level: whether this is the top-level field container, defaults to False
        """
        super().__init__(name, description=description, required=required, custom_field_tags=custom_field_tags)

        if not top_level and any([f.is_heavy_pointer for f in fields]):
            raise WickerSchemaException(
                f"Heavy pointer fields cannot be nested in a "
                f"RecordField: {[f.name for f in fields if f.is_heavy_pointer]}"
            )

        self.fields: List[SchemaField] = fields

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_record_field(self)

    def __eq__(self, other: Any) -> bool:
        return (
            super().__eq__(other)
            and len(self.fields) == len(other.fields)
            and all([self_field == other_field for self_field, other_field in zip(self.fields, other.fields)])
        )


class ArrayField(SchemaField):
    """A field that contains an array of values (that have the same schema type)"""

    def __init__(self, element_field: SchemaField, required: bool = True):
        super().__init__(
            element_field.name,
            description=element_field.description,
            required=required,
            custom_field_tags=element_field.custom_field_tags,
        )
        self.element_field = element_field

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_array_field(self)

    def __eq__(self, other: Any) -> bool:
        return super().__eq__(other) and self.element_field == other.element_field


class ObjectField(SchemaField):
    """A field that contains objects of a specific type."""

    def __init__(
        self,
        name: str,
        codec: codecs.Codec,
        description: str = "",
        required: bool = True,
        is_heavy_pointer: bool = True,
    ) -> None:
        """Create an ObjectField. The ObjectField is parametrized with a Codec that it will use when
        serializing/deserializing data to/from storage. Users are responsible for providing the Codec
        at both write and read time.

        :param name: name of the field
        :param codec: Encoder/decoder to serialize/deserialize the object. See codecs.Codec for more details on
        the codecs.
        """
        if not codec.get_codec_name():
            raise WickerSchemaException("Codec names must be non-empty. Encountered at field={name}")

        custom_field_tags = {
            "_l5ml_metatype": "object",
            "_codec_params": json.dumps(codec.save_codec_to_dict()),
            "_codec_name": codec.get_codec_name(),
        }
        super().__init__(
            name,
            description=description,
            required=required,
            custom_field_tags=custom_field_tags,
        )
        self._is_heavy_pointer = is_heavy_pointer
        self.codec = codec

    @property
    def is_heavy_pointer(self) -> bool:
        """Whether to store this field as a separate file"""
        return self._is_heavy_pointer

    def accept_visitor(self, visitor: DatasetSchemaVisitor[_T]) -> _T:
        """Processes the current schema field with the visitor object"""
        return visitor.process_object_field(self)

    def __eq__(self, other: Any) -> bool:
        return super().__eq__(other) and self.codec == other.codec


class DatasetSchema:
    """A schema definition that serializes into an Avro-compatible schema"""

    def __init__(
        self,
        fields: List[SchemaField],
        primary_keys: List[str],
        allow_empty_primary_keys: bool = False,
    ) -> None:
        """Create a new dataset schema.
        :param field: List of fields at the top level of the structure.
        :param primary_keys: List of field names to use to order the data. Keys must be listed in order of precedence,
            and must be of type int, long, str or bool.
            The keys are used to uniquely identify examples, they are also used when joining multiple datasets.
        :param allow_empty_primary_keys: Internal use. Do not change.
        """
        self.schema_record = RecordField(
            name="fields",
            fields=fields,
            custom_field_tags={
                PRIMARY_KEYS_TAG: json.dumps(primary_keys),
            },
            top_level=True,
        )
        self._columns = {f.name: f for f in fields}
        self.primary_keys = primary_keys
        self._validate_schema(allow_empty_primary_keys)

    def _validate_schema(self, allow_empty_primary_keys: bool) -> None:
        # We want all new datasets to have primary keys. But the allow_empty_primary_keys flag allows us to construct
        # DatasetSchemas from existing files which did not have this properly specified. Clean me AVSW-78939.
        if not allow_empty_primary_keys and not self.primary_keys:
            raise WickerSchemaException("The primary_keys attribute can not be empty.")
        for key in self.primary_keys:
            if key not in self._columns:
                raise WickerSchemaException(f"Primary key '{key}' not found in the schema's fields.")
            field = self._columns[key]
            if not field.required:
                raise WickerSchemaException(
                    f"All primary key fields must have the 'required' tag, but '{key}' doesn't have it."
                )
            # In particular, we don't accept floats/double as keys since inconsistent rounding could be an issue.
            supported_types = [IntField, LongField, StringField, BoolField]
            if not any(isinstance(field, t) for t in supported_types):
                raise WickerSchemaException(
                    f"'{key}' cannot be a primary key because it is not of type int, long, string or bool."
                )

    def get_all_column_names(self) -> List[str]:
        """Returns all column names"""
        return list(self._columns.keys())

    def get_column(self, column_name: str) -> Optional[SchemaField]:
        """Get a column by name"""
        return self._columns.get(column_name)

    def get_pointer_columns(self) -> List[str]:
        """Get the list of columms that contain heavy pointer data."""
        return [col_name for col_name, field in self._columns.items() if field.is_heavy_pointer]

    def get_non_pointer_columns(self) -> List[str]:
        """Get the list of columms that don't contain heavy pointer data. These are usually small metadata columns."""
        return [col_name for col_name, field in self._columns.items() if not field.is_heavy_pointer]

    def __eq__(self, other: Any) -> bool:
        return (
            isinstance(other, DatasetSchema)
            and self.schema_record == other.schema_record
            and self.primary_keys == other.primary_keys
        )


class DatasetSchemaVisitor(abc.ABC, Generic[_T]):
    """Class that operates over SchemaField objects using the Visitor pattern"""

    @abc.abstractmethod
    def process_int_field(self, field: IntField) -> _T:
        pass

    @abc.abstractmethod
    def process_long_field(self, field: LongField) -> _T:
        pass

    @abc.abstractmethod
    def process_string_field(self, field: StringField) -> _T:
        pass

    @abc.abstractmethod
    def process_bool_field(self, field: BoolField) -> _T:
        pass

    @abc.abstractmethod
    def process_float_field(self, field: FloatField) -> _T:
        pass

    @abc.abstractmethod
    def process_double_field(self, field: DoubleField) -> _T:
        pass

    @abc.abstractmethod
    def process_object_field(self, field: ObjectField) -> _T:
        pass

    @abc.abstractmethod
    def process_record_field(self, field: RecordField) -> _T:
        pass

    @abc.abstractmethod
    def process_array_field(self, field: ArrayField) -> _T:
        pass
