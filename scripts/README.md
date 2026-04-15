# META-COMPILER Document Scripts

Utility scripts for reading and writing common document formats. These scripts
enable the META-COMPILER pipeline to ingest and produce documents in formats
that non-technical users already work with.

## Prerequisites

```bash
pip install -r requirements.txt
```

## Reading Documents

Extract full text from any supported document format:

```bash
# Print extracted text to stdout
python scripts/read_document.py path/to/document.pdf

# Write extracted text to a file
python scripts/read_document.py path/to/document.docx --output extracted.txt

# Extract a spreadsheet
python scripts/read_document.py data.xlsx --output data.txt
```

**Supported input formats:** `.docx`, `.xlsx`, `.pptx`, `.pdf`, `.txt`, `.md`, `.rst`, `.tex`, `.csv`

## Writing Documents

Create documents from plain text:

```bash
# Write from a text file
python scripts/write_document.py output.docx --input content.txt --title "My Report"

# Pipe text from stdin
echo "Hello world" | python scripts/write_document.py output.pdf --title "Greeting"

# Create a presentation
python scripts/write_document.py slides.pptx --input notes.txt --title "Presentation"
```

**Supported output formats:** `.docx`, `.xlsx`, `.pptx`, `.pdf`

## Use in the Pipeline

These scripts are called automatically by the META-COMPILER pipeline:
- **Stage 1A** can extract text from non-plaintext seed documents
- **Stage 3** scaffold generation can call these for document output
- **Stage 4** uses the pptx writer for pitch deck generation
- **Any agent** can call `read_document.py` to extract seed content

## Integration with Agents

Any agent in the pipeline can invoke these scripts:

```bash
# Extract a PDF seed for wiki ingestion
python scripts/read_document.py workspace-artifacts/seeds/paper.pdf --output /tmp/paper_text.md

# Generate a report document from wiki content
python scripts/write_document.py workspace-artifacts/executions/v1/report.docx --input report.md
```
