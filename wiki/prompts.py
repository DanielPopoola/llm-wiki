"""
All LLM prompts for the wiki agent.

"""


def extraction_prompt(source_text: str) -> str:
    """Node: extract_entities_and_concepts"""
    return f"""\
You are an expert knowledge base builder. Read the following source document \
and extract structured information.

Source document:
---
{source_text}
---

Extract:
- Named entities: people, companies, organisations, products
- Key concepts: topics and themes the document is about
- Key claims: the 5-10 most important factual assertions a reader must know

Return only the structured data requested. No commentary."""


def summary_page_prompt(
    source_text: str,
    key_claims: list[str],
    entities: list[str],
    source_filename: str,
) -> str:
    """Node: write_summary_page"""
    claims_text = "\n".join(f"- {c}" for c in key_claims)
    entities_wikilinks = ", ".join(f"[[{e}]]" for e in entities)

    return f"""\
Write a structured wiki summary page for this source document.

Source text:
---
{source_text}
---

Key claims extracted:
{claims_text}

Entities mentioned: {entities_wikilinks}

Write a markdown page with exactly these sections:

## Overview
(2-3 sentence summary of what this source is about)

## Key Claims
(bullet list of the key claims above, verbatim)

## Entities Mentioned
(wikilinks to each entity using [[Entity Name]] format)

## Source
{source_filename}

Write only the markdown body. No frontmatter. No preamble."""


def new_entity_page_prompt(
    entity: str,
    source_text: str,
    relevant_claims: list[str],
    related_entities: list[str],
) -> str:
    """Node: update_entity_pages — new page"""
    claims_text = "\n".join(f"- {c}" for c in relevant_claims)
    related_links = ", ".join(f"[[{e}]]" for e in related_entities)

    return f"""\
Write a wiki page for the entity "{entity}".

Based on this source document:
---
{source_text}
---

Relevant claims about {entity}:
{claims_text}

Write a structured markdown page with exactly these sections:

## Overview
(who or what is this entity, in 2-3 sentences)

## Key Facts
(bullet list of facts from the source)

## Related
{related_links}

Write only the page body. No frontmatter. No preamble."""


def update_entity_page_prompt(
    entity: str,
    existing_content: str,
    source_text: str,
    relevant_claims: list[str],
) -> str:
    """Node: update_entity_pages — existing page"""
    claims_text = "\n".join(f"- {c}" for c in relevant_claims)

    return f"""\
Update this existing wiki entity page with new information from a source document.

Existing page for "{entity}":
---
{existing_content}
---

New source document:
---
{source_text}
---

New claims about {entity}:
{claims_text}

Instructions:
- Keep existing content that is still accurate
- Integrate new information naturally into the existing sections
- If any new claims contradict existing ones, add them under ## Contradictions
- Use [[Entity Name]] wikilinks when referencing other entities

Write only the updated page body. No frontmatter. No preamble."""


def new_topic_page_prompt(
    concept: str,
    source_text: str,
    key_claims: list[str],
    entities: list[str],
    source_stem: str,
) -> str:
    """Node: update_topic_pages — new page"""
    entity_links = ", ".join(f"[[{e}]]" for e in entities)

    return f"""\
Write a topic overview page for "{concept}".

Based on this source document:
---
{source_text}
---

Write a structured markdown page with exactly these sections:

## Overview
(what is this topic, in 2-3 sentences)

## Key Entities
{entity_links}

## Key Themes
(bullet list of the main themes and ideas from the source)

## Sources
- [[{source_stem}]]

Write only the page body. No frontmatter. No preamble."""


def update_topic_page_prompt(
    concept: str,
    existing_content: str,
    entities: list[str],
    key_claims: list[str],
    source_stem: str,
) -> str:
    """Node: update_topic_pages — existing page"""
    entity_links = ", ".join(f"[[{e}]]" for e in entities)
    claims_text = "\n".join(f"  - {c}" for c in key_claims)

    return f"""\
Update this topic overview page to reflect a new source document.

Existing topic page for "{concept}":
---
{existing_content}
---

New source adds:
- Entities: {entity_links}
- Key claims:
{claims_text}
- Source: [[{source_stem}]]

Instructions:
- Integrate the new source naturally into the existing content
- Add [[{source_stem}]] to the ## Sources section
- Use [[Entity Name]] wikilinks throughout

Write only the updated page body. No frontmatter. No preamble."""


def contradiction_check_prompt(
    entity: str,
    existing_content: str,
    new_claims: list[str],
) -> str:
    """Node: flag_contradictions"""
    claims_text = "\n".join(f"- {c}" for c in new_claims)

    return f"""\
Compare these two sets of claims about "{entity}" and identify any direct contradictions.

Existing wiki page for {entity}:
---
{existing_content}
---

New claims from latest source:
{claims_text}

A contradiction is when two claims make mutually exclusive factual assertions \
about the same thing — different figures for the same metric, conflicting dates, \
opposing outcomes. Updated figures across different time periods (Q2 vs Q3) are \
NOT contradictions.

Return only the structured data requested. No commentary."""


# ---------------------------------------------------------------------------
# Index prompts
# ---------------------------------------------------------------------------


def page_description_prompt(title: str, page_type: str, body: str) -> str:
    """Node: update_index — one-line description per new/updated page."""
    return f"""\
Write a single-line description for this wiki {page_type} page. \
Maximum 12 words. No punctuation at the end. No quotes.

Page title: {title}

Page content:
---
{body[:800]}
---

Return only the one-line description. Nothing else."""
