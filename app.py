from flask import Flask, render_template, request, send_from_directory
import os
from felderkennung import get_form_fields

app = Flask(__name__)
UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

@app.route("/")
def index():
    return render_template("interface.html", filename=None, fields=None)

@app.route("/upload", methods=["POST"])
def upload():
    if "pdf_file" not in request.files:
        return "Keine Datei gesendet"
    pdf = request.files["pdf_file"]
    if pdf.filename == "":
        return "Keine Datei ausgewählt"

    filepath = os.path.join(UPLOAD_FOLDER, pdf.filename)
    pdf.save(filepath)

    fields = get_form_fields(filepath)

    return render_template("interface.html", filename=pdf.filename, fields=fields)

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(UPLOAD_FOLDER, filename)

if __name__ == "__main__":
    app.run(debug=True, port=5050)

