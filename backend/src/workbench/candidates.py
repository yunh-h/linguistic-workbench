"""Local v0.1 vocabulary and structural checks for optional LLM alternatives.

This is a conservative supported subset, not a linguistic correctness oracle.
Parser output is not rewritten or rejected by these candidate-only allowlists.
Sources: https://universaldependencies.org/u/feat/index.html
         https://universaldependencies.org/u/dep/index.html
         https://universaldependencies.org/fr/index.html
"""

import json

from pydantic import TypeAdapter

from workbench.schema import Analysis, Dependency, UPOS


FEATURE_VALUES = {
    "Abbr": {"Yes"}, "Foreign": {"Yes"}, "Typo": {"Yes"},
    "PronType": {"Art", "Dem", "Emp", "Exc", "Ind", "Int", "Neg", "Prs", "Rcp", "Rel", "Tot"},
    "NumType": {"Card", "Dist", "Frac", "Mult", "Ord", "Range", "Sets"},
    "Poss": {"Yes"}, "Reflex": {"Yes"},
    "Gender": {"Com", "Fem", "Masc", "Neut"},
    "Number": {"Sing", "Plur", "Dual"},
    "Case": {"Nom", "Acc", "Dat", "Gen", "Loc", "Ins", "Abl", "Voc", "Com"},
    "Definite": {"Def", "Ind"}, "Degree": {"Pos", "Cmp", "Sup"},
    "VerbForm": {"Fin", "Inf", "Part", "Ger", "Conv", "Vnoun"},
    "Mood": {"Ind", "Imp", "Sub", "Cnd", "Pot"},
    "Tense": {"Past", "Imp", "Pres", "Fut", "Pqp"},
    "Aspect": {"Imp", "Perf", "Prog", "Hab"},
    "Voice": {"Act", "Pass", "Mid", "Cau"},
    "Polarity": {"Pos", "Neg"}, "Person": {"1", "2", "3"},
    "Polite": {"Infm", "Form", "Elev", "Humb"},
}
# Explicit French layered features; no arbitrary bracketed namespaces.
FR_FEATURES = {
    name: FEATURE_VALUES[base]
    for base, layers in {"Number": ("psor", "cxtx", "lex"), "Person": ("psor",),
                         "Gender": ("cxtx", "lex"), "Tense": ("denom",)}.items()
    for name in (f"{base}[{layer}]" for layer in layers)
}
BASIC_RELATIONS = set("""acl advcl advmod amod appos aux case cc ccomp clf compound conj cop
    csubj dep det discourse dislocated expl fixed flat goeswith iobj list mark nmod
    nsubj nummod obj obl orphan parataxis punct reparandum root vocative xcomp""".split())
RELATION_SUBTYPES = {
    "fr": set("""acl:relcl advcl:relcl advcl:cleft aux:caus aux:pass aux:tense csubj:outer
        csubj:pass dep:comp expl:comp expl:impers expl:pass expl:pv expl:subj flat:foreign
        flat:name iobj:agent nmod:appos nsubj:caus nsubj:outer nsubj:pass obj:agent
        obj:lvc obl:agent obl:arg obl:mod parataxis:insert parataxis:parenth""".split()),
    "ja": {"acl:relcl", "advcl:relcl", "aux:pass", "csubj:outer", "csubj:pass",
           "nsubj:outer", "nsubj:pass", "obl:agent"},
}
_TYPES = {"lemma": TypeAdapter(str), "upos": TypeAdapter(UPOS),
          "morphology": TypeAdapter(dict[str, list[str]]), "dependency": TypeAdapter(Dependency)}


def decode_value(text: str):
    def object_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON property")
            result[key] = value
        return result

    def constant(value):
        raise ValueError("non-JSON numeric constant")

    return json.loads(text, object_pairs_hook=object_pairs, parse_constant=constant)


def vocabulary(language: str) -> dict:
    return {
        "morphology": {k: sorted(v) for k, v in
                       (FEATURE_VALUES | (FR_FEATURES if language == "fr" else {})).items()},
        "dependency_relations": sorted(BASIC_RELATIONS | RELATION_SUBTYPES.get(language, set())),
        "upos": _TYPES["upos"].json_schema()["enum"],
    }


def validate_value(field: str, value, language: str, word_id: str, sentence_words: set[str]):
    """Return the typed value, rejecting coercion, unknown vocabulary and bad edges."""
    if field not in _TYPES:
        raise ValueError("unsupported proposal field")
    if isinstance(value, Dependency):
        value = value.model_dump()
    value = _TYPES[field].validate_python(value, strict=True)
    if field == "lemma" and not value.strip():
        raise ValueError("empty lemma")
    if field == "morphology":
        allowed = FEATURE_VALUES | (FR_FEATURES if language == "fr" else {})
        for feature, values in value.items():
            if (feature not in allowed or not values or len(values) != len(set(values)) or
                    not set(values) <= allowed[feature]):
                raise ValueError("unsupported morphology feature or value")
    if field == "dependency":
        if value.relation not in BASIC_RELATIONS | RELATION_SUBTYPES.get(language, set()):
            raise ValueError("unsupported dependency relation")
        if (value.head_word_id is None) != (value.relation == "root"):
            raise ValueError("root and null head must coincide")
        if value.head_word_id is not None and (value.head_word_id not in sentence_words or value.head_word_id == word_id):
            raise ValueError("missing, cross-sentence or self head")
    return value


def values_equal(field: str, left, right) -> bool:
    if field == "morphology" and left is not None and right is not None:
        return {k: set(v) for k, v in left.items()} == {k: set(v) for k, v in right.items()}
    return left == right


def validate_dependency_overlay(analysis: Analysis, alternatives: dict[str, Dependency]) -> None:
    """Check the batch jointly against retained parser edges, without modifying them.

    A coordinated root/head change can be valid even when its individual edits
    would not be. Unknown parser edges stay missing; this never fills them in.
    """
    for sentence in analysis.sentences:
        if not any(word.id in alternatives for word in sentence.words):
            continue
        edges = {}
        roots = 0
        present_count = 0
        for word in sentence.words:
            dep = alternatives.get(word.id, word.dependency.value)
            if dep is None:
                continue
            present_count += 1
            if dep.head_word_id is None:
                roots += 1
            else:
                edges[word.id] = dep.head_word_id
        if roots > 1 or (present_count == len(sentence.words) and roots != 1):
            raise ValueError("invalid candidate root count")
        for start in edges:
            seen = set()
            at = start
            while at in edges:
                if at in seen:
                    raise ValueError("candidate dependency cycle")
                seen.add(at)
                at = edges[at]
