import functools
import inspect
import json
import os
from contextlib import contextmanager

import gradio as gr
import yaml
from gradio.blocks import Block, BlockContext, Context


# Monkey patch to escape I18nString type being stripped in gradio.Markdown
def escape_caller(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        if args and isinstance(args[0], I18nString):
            return I18nString(func(*args, **kwargs))
        return func(*args, **kwargs)

    return wrapper


inspect.cleandoc = escape_caller(inspect.cleandoc)


class I18nString(str):
    def __str__(self):
        return self


def gettext(key: str):
    """Wrapper text string to return I18nString
    :param key: The key of the I18nString
    """
    return I18nString(key)


def iter_i18n_choices(choices):
    """Iterate all I18nStrings in the choice, returns the indices of the I18nStrings"""
    if not isinstance(choices, list) or len(choices) == 0:
        return

    if isinstance(choices[0], tuple):
        for i, (k, v) in enumerate(choices):
            if isinstance(k, I18nString):
                yield i

    else:
        for i, v in enumerate(choices):
            if isinstance(v, I18nString):
                yield i


def iter_i18n_fields(component: gr.components.Component):
    """Iterate all I18nStrings in the component"""
    for name, value in inspect.getmembers(component):
        if name == "value" and hasattr(component, "choices"):
            # for those components with choices, the value will be kept as is
            continue
        if isinstance(value, I18nString):
            yield name
        elif name == "choices" and any(iter_i18n_choices(value)):
            yield name


def iter_i18n_components(block: Block):
    """Iterate all I18nStrings in the block"""
    if isinstance(block, BlockContext):
        for component in block.children:
            for c in iter_i18n_components(component):
                yield c

    if any(iter_i18n_fields(block)):
        yield block


def has_new_i18n_fields(block: Block, langs=["en"], existing_translation={}):
    """Check if there are new I18nStrings in the block
    :param block: The block to check
    :param langs: The languages to check
    :param existing_translation: The existing translation dictionary
    :return: True if there are new I18nStrings, False otherwise
    """
    components = list(iter_i18n_components(block))

    for lang in langs:
        for component in components:
            for field in iter_i18n_fields(component):
                if field == "choices":
                    for idx in iter_i18n_choices(component.choices):
                        if isinstance(component.choices[idx], tuple):
                            value = component.choices[idx][0]
                        else:
                            value = component.choices[idx]
                        if value not in existing_translation.get(lang, {}):
                            return True
                else:
                    value = getattr(component, field)
                    if value not in existing_translation.get(lang, {}):
                        return True

    return False


def dump_blocks(block: Block, langs=["en"], include_translations={}):
    """Dump all I18nStrings in the block to a dictionary
    :param block: The block to dump
    :param langs: The languages to dump
    :param include_translations: The existing translation dictionary
    :return: The dumped dictionary
    """
    components = list(iter_i18n_components(block))

    def translate(lang, key):
        return include_translations.get(lang, {}).get(key, key)

    ret = {}

    for lang in langs:
        ret[lang] = {}
        for component in components:
            for field in iter_i18n_fields(component):
                if field == "choices":
                    for idx in iter_i18n_choices(component.choices):
                        if isinstance(component.choices[idx], tuple):
                            value = component.choices[idx][0]
                        else:
                            value = component.choices[idx]
                        value = "" + value
                        ret[lang][value] = translate(lang, value)
                else:
                    value = "" + getattr(component, field)
                    ret[lang][value] = translate(lang, value)

    return ret


def translate_blocks(
    block: gr.Blocks = None, translation={}, lang: gr.components.Component = None
):
    """Translate all I18nStrings in the block
    :param block: The block to translate, default is the root block
    :param translation: The translation dictionary
    :param lang: The language component to change the language
    """
    if block is None:
        block = Context.root_block

    """Translate all I18nStrings in the block"""
    if not isinstance(block, gr.Blocks):
        raise ValueError("block must be an instance of gradio.Blocks")

    components = list(iter_i18n_components(block))

    def translate(lang, key):
        return translation.get(lang, {}).get(key, key)

    def on_load(request: gr.Request):
        lang = request.headers["Accept-Language"].split(",")[0].split("-")[0].lower()
        if not lang:
            return "en"
        return lang

    def on_lang_change(lang: str):
        outputs = []
        for component in components:
            fields = list(iter_i18n_fields(component))
            if component == lang and "value" in fields:
                raise ValueError("'lang' component can't has I18nStrings as value")

            modified = {}

            for field in fields:
                if field == "choices":
                    choices = component.choices.copy()
                    for idx in iter_i18n_choices(choices):
                        if isinstance(choices[idx], tuple):
                            k, v = choices[idx]
                            choices[idx] = (translate(lang, k), v)
                        else:
                            v = choices[idx]
                            choices[idx] = (translate(lang, v), v)
                    modified[field] = choices
                else:
                    modified[field] = translate(lang, getattr(component, field))

            new_comp = gr.update(**modified)
            outputs.append(new_comp)

        if len(outputs) == 1:
            return outputs[0]

        return outputs

    if lang is None:
        lang = gr.State()

    block.load(on_load, outputs=[lang])
    lang.change(on_lang_change, inputs=[lang], outputs=components)


@contextmanager
def Translate(translation, lang: gr.components.Component = None, placeholder_langs=[]):
    """Translate all I18nStrings in the block
    :param translation: The translation dictionary or file path
    :param lang: The language component to change the language
    :param placeholder_langs: The placeholder languages to create a new translation file if translation is a file path
    :return: The language component
    """
    if lang is None:
        lang = gr.State()
    yield lang

    if isinstance(translation, dict):
        # Static translation
        translation_dict = translation
        pass
    elif isinstance(translation, str):
        if os.path.exists(translation):
            # Regard as a file path
            with open(translation, "r") as f:
                if translation.endswith(".json"):
                    translation_dict = json.load(f)
                elif translation.endswith(".yaml"):
                    translation_dict = yaml.safe_load(f)
                else:
                    raise ValueError("Unsupported file format")
        else:
            translation_dict = {}
    else:
        raise ValueError("Unsupported translation type")

    block = Context.block
    translate_blocks(block=block, translation=translation_dict, lang=lang)

    if (
        placeholder_langs
        and isinstance(translation, str)
        and has_new_i18n_fields(
            block, langs=placeholder_langs, existing_translation=translation_dict
        )
    ):
        merged = dump_blocks(
            block, langs=placeholder_langs, include_translations=translation_dict
        )

        with open(translation, "w") as f:
            if translation.endswith(".json"):
                json.dump(merged, f, indent=2, ensure_ascii=False)
            elif translation.endswith(".yaml"):
                yaml.dump(merged, f, allow_unicode=True, sort_keys=False)
