"""gloss — structured lexical retrieval from documents.

Turn a source text into a portable, cited, principle-anchored corpus that an
agent can query for the relevant primary-source passages given a situation.

This package is the corpus-agnostic *engine*. A specific corpus (e.g. APOSD) is
an *instance* configured under ``corpora/<name>/`` (parse profile, taxonomy,
enrichment prompt, eval cases). See ``docs/superpowers/specs/`` for the design.
"""

__version__ = "0.1.0"
