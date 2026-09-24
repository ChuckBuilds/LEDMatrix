"""coerce_array_shapes: position-keyed dicts back into lists before validation."""

from src.web_interface.config_arrays import coerce_array_shapes

COLOR = {"type": "array", "items": {"type": "integer"},
         "minItems": 3, "maxItems": 3, "default": [255, 255, 255]}


def test_position_keys_become_a_list_in_numeric_order():
    config = {"tags": {"10": "k", "2": "c", "0": "a"}}
    coerce_array_shapes(config, {"tags": {"type": "array"}})
    assert config["tags"] == ["a", "c", "k"]


def test_an_empty_dict_becomes_an_empty_list():
    config = {"tags": {}}
    coerce_array_shapes(config, {"tags": {"type": "array"}})
    assert config["tags"] == []


def test_a_dict_with_other_keys_is_left_for_validation_to_report():
    config = {"tags": {"0": "a", "name": "b"}}
    coerce_array_shapes(config, {"tags": {"type": "array"}})
    assert config["tags"] == {"0": "a", "name": "b"}


def test_element_types_are_left_to_normalization():
    config = {"color": {"0": "1", "1": "2", "2": "3"}}
    coerce_array_shapes(config, {"color": COLOR})
    assert config["color"] == ["1", "2", "3"]


def test_nested_objects_and_array_items_are_walked():
    schema = {"feeds": {"type": "object", "properties": {
        "custom_feeds": {"type": "array", "items": {"type": "object", "properties": {
            "tags": {"type": "array"},
        }}},
    }}}
    config = {"feeds": {"custom_feeds": {"0": {"tags": {"0": "news"}}}}}
    coerce_array_shapes(config, schema)
    assert config == {"feeds": {"custom_feeds": [{"tags": ["news"]}]}}


def test_a_short_form_list_takes_the_default_only_when_asked():
    config = {"color": ["10", "20"]}
    coerce_array_shapes(config, {"color": COLOR})
    assert config["color"] == ["10", "20"]

    coerce_array_shapes(config, {"color": COLOR}, short_lists_take_default=True)
    assert config["color"] == [255, 255, 255]
    assert config["color"] is not COLOR["default"]


def test_a_default_too_short_itself_is_not_used():
    schema = {"color": dict(COLOR, default=[0])}
    config = {"color": ["10"]}
    coerce_array_shapes(config, schema, short_lists_take_default=True)
    assert config["color"] == ["10"]
