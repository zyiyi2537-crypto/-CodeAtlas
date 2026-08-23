# CodeAtlas demo document

This is a small synthetic document used to verify document ingestion and retrieval.

## Retrieval contract

A document result should include its collection, title, section, page when available,
and a bounded text excerpt. The service must not expose API keys, passwords, session
cookies, or database credentials in an indexed document.

## Example workflow

1. Create a document collection.
2. Upload this Markdown file.
3. Run a document search for `retrieval contract`.
4. Confirm that the result includes this section and its source title.
