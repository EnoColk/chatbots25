from PyPDF2 import PdfReader

def get_form_fields(pdf_path):
    reader = PdfReader(pdf_path)
    fields = {}
    if reader.get_fields():
        for key, field in reader.get_fields().items():
            fields[key] = field.get('/V') or ''
    return fields
