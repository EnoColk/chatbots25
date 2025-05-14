from PyPDF2 import PdfReader
from pdf2image import convert_from_path
from PIL import Image
import pytesseract
import os

def get_form_fields(pdf_path):
    fields = {}

    try:
        reader = PdfReader(pdf_path)
        if reader.get_fields():
            for key, field in reader.get_fields().items():
                fields[key] = field.get('/V') or ''
            return fields
        else:
            # If no form fields, fallback to text extraction
            print("No form fields found. Trying text extraction...")
            return {"Text": extract_text_from_pdf(pdf_path)}
    except Exception as e:
        print(f"Error with PyPDF2: {e}")
        return {"Error": "Unable to extract form fields or text."}

def extract_text_from_pdf(pdf_path):
    # Try to extract text using PyPDF2 first
    try:
        reader = PdfReader(pdf_path)
        text = ""
        for page in reader.pages:
            text += page.extract_text() or ""
        if text.strip():
            return text.strip()
    except Exception as e:
        print(f"Text extraction failed: {e}")

    # If text is empty, try OCR
    print("Falling back to OCR...")
    try:
        images = convert_from_path(pdf_path)
        ocr_text = ""
        for image in images:
            ocr_text += pytesseract.image_to_string(image)
        return ocr_text.strip()
    except Exception as e:
        print(f"OCR failed: {e}")
        return "Unable to read PDF."
