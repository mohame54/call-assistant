import inspect
import typing
import json
import dataclasses
import pydantic
from typing import Any, Dict, List, Optional, Union, get_origin, get_args

JSONType = Dict[str, Any]

PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    dict: "object",
    list: "array",
}

NoneType = type(None)


def _is_optional_annotation(annotation) -> bool:
    origin = get_origin(annotation)
    if origin is Union:
        return NoneType in get_args(annotation)
    return False


def _resolve_forward_ref(annotation, globalns):
    try:
        if isinstance(annotation, typing.ForwardRef):
            return eval(annotation.__forward_arg__, globalns)
    except Exception:
        pass
    return annotation


def _is_typed_dict(cls) -> bool:
    # TypedDict in runtime is just a class with __annotations__ and a _is_typeddict attribute in newer python
    return isinstance(cls, type) and hasattr(cls, "__annotations__") and getattr(cls, "__total__", None) is not None


def _is_pydantic_model(cls) -> bool:
    try:
        return isinstance(cls, type) and issubclass(cls, pydantic.BaseModel)
    except Exception:
        return False


def _json_schema_for_annotation(annotation, *, globalns=None) -> JSONType:
    if annotation is inspect._empty:
        return {}

    if isinstance(annotation, str) or getattr(annotation, "__forward_arg__", None):
        try:
            # Attempt get_type_hints style resolution if globalns provided
            if globalns:
                resolved = typing.get_type_hints(lambda: None, globalns=globalns).get("__annotations__", {}).get(annotation, None)
                if resolved:
                    annotation = resolved
            # last resort: use eval (dangerous in general; here used only on annotations)
            annotation = eval(annotation, globalns or {})
        except Exception:
            # leave as-is if cannot resolve
            pass

    # Optional[T] -> treat as T for schema generation; outer code will mark param not-required when Optional
    if _is_optional_annotation(annotation):
        non_none_args = [a for a in get_args(annotation) if a is not NoneType]
        if not non_none_args:
            return {}
        if len(non_none_args) == 1:
            return _json_schema_for_annotation(non_none_args[0], globalns=globalns)
        # multiple non-none union members -> fall through to Union handling below

    origin = get_origin(annotation)

    # Union[...] (non-None handled above) -> oneOf
    if origin is Union:
        schemas = []
        for arg in get_args(annotation):
            if arg is NoneType:
                continue
            schemas.append(_json_schema_for_annotation(arg, globalns=globalns))
        if not schemas:
            return {}
        if len(schemas) == 1:
            return schemas[0]
        return {"oneOf": schemas}

    # Literal[...] -> enum
    if getattr(typing, "Literal", None) and origin is typing.Literal:
        literals = list(get_args(annotation))
        return {"enum": literals}

    # Lists / Sequences / tuple[...] (simple support)
    if origin in (list, List, typing.Sequence, tuple):
        args = get_args(annotation)
        item_schema = _json_schema_for_annotation(args[0], globalns=globalns) if args else {}
        return {"type": "array", "items": item_schema}

    # Dict / Mapping -> object with additionalProperties
    if origin in (dict, Dict, typing.Mapping):
        args = get_args(annotation)
        if len(args) >= 2:
            # keys_schema = _json_schema_for_annotation(args[0], globalns=globalns)  # usually string
            value_schema = _json_schema_for_annotation(args[1], globalns=globalns)
            return {"type": "object", "additionalProperties": value_schema}
        return {"type": "object"}

    # TypedDict (class with __annotations__ and __total__)
    if _is_typed_dict(annotation):
        props = {}
        required = []
        ann = annotation.__annotations__
        total = getattr(annotation, "__total__", True)
        for name, sub_ann in ann.items():
            props[name] = _json_schema_for_annotation(sub_ann, globalns=globalns)
            if total:
                required.append(name)
        schema: JSONType = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    # dataclass
    if dataclasses.is_dataclass(annotation):
        props = {}
        required = []
        for f in dataclasses.fields(annotation):
            props[f.name] = _json_schema_for_annotation(f.type, globalns=globalns)
            if f.default is dataclasses.MISSING and f.default_factory is dataclasses.MISSING:
                required.append(f.name)
        schema: JSONType = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    # pydantic BaseModel
    if _is_pydantic_model(annotation):
        # Best-effort: inspect model __fields__ for types and required flags
        props = {}
        required = []
        for name, model_field in annotation.__fields__.items():
            # model_field.outer_type_ is the declared type
            props[name] = _json_schema_for_annotation(model_field.outer_type_, globalns=globalns)
            if model_field.required:
                required.append(name)
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    # basic python primitives
    if annotation in PY_TO_JSON:
        return {"type": PY_TO_JSON[annotation]}

    # fallback for classes with __annotations__ (custom classes used as simple containers)
    if isinstance(annotation, type) and hasattr(annotation, "__annotations__"):
        props = {}
        required = []
        ann = getattr(annotation, "__annotations__", {})
        for name, sub_ann in ann.items():
            props[name] = _json_schema_for_annotation(sub_ann, globalns=globalns)
            # we can't reliably know defaults here; treat all as required (best-effort)
            required.append(name)
        schema = {"type": "object", "properties": props}
        if required:
            schema["required"] = required
        return schema

    # fallback: unknown complex type -> generic object
    return {"type": "object"}


def generate_tool_spec(func: typing.Callable, *, name: Optional[str] = None,
                       description: Optional[str] = None,
                       param_descriptions: Optional[Dict[str, str]] = None) -> JSONType:
    sig = inspect.signature(func)
    # ensure forward refs are resolved relative to the function globals
    globalns = getattr(func, "__globals__", None) or {}

    hints = typing.get_type_hints(func, globalns=globalns, include_extras=True)
    param_descriptions = param_descriptions or {}

    properties: Dict[str, JSONType] = {}
    required: List[str] = []

    for pname, param in sig.parameters.items():
        if pname == "self":
            continue
        if param.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD):
            continue

        ann = hints.get(pname, param.annotation)
        schema = _json_schema_for_annotation(ann, globalns=globalns)

        # add description if provided
        if pname in param_descriptions:
            schema["description"] = param_descriptions[pname]

        if param.default is not inspect._empty:
            default_val = param.default
            try:
                json.dumps(default_val)
                schema["default"] = default_val
            except Exception:
                pass
        else:
            if not _is_optional_annotation(ann):
                required.append(pname)

        properties[pname] = schema

    tool_name = name or func.__name__
    tool_description = description or (func.__doc__.strip().splitlines()[0] if func.__doc__ else "")

    parameters: JSONType = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required

    return {
        "name": tool_name,
        "description": tool_description,
        "parameters": parameters
    }


def openai_tool(*, name: Optional[str] = None, description: Optional[str] = None,
                param_descriptions: Optional[Dict[str, str]] = None):
    def decorator(func: typing.Callable):
        spec = generate_tool_spec(func, name=name, description=description, param_descriptions=param_descriptions)
        setattr(func, "__openai_tool__", spec)
        return func
    return decorator
