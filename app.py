import os
import io
from flask import Flask, request, render_template, redirect, url_for, send_from_directory, session, jsonify
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject, DictionaryObject, BooleanObject
from dotenv import load_dotenv

# API-Key laden (falls benötigt)
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Formularfelder extrahieren
def extract_form_fields(pdf_path):
    reader = PdfReader(pdf_path)
    fields = {}
    for field in reader.get_fields().values():
        name = field.get('/T')
        value = field.get('/V', '')
        if name:
            fields[name] = value or ''
    return fields

# Hauptseite
@app.route("/", methods=["GET"])
def index():
    filename = session.get("filename")
    return render_template("interface.html", filename=filename)

# PDF-Upload
@app.route("/upload", methods=["POST"])
def upload_pdf():
    if "pdf_file" not in request.files:
        return "Keine Datei hochgeladen", 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return "Leerer Dateiname", 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)

    session["filename"] = filename
    session["field_state"] = {"_asked": [], "_answered": {}}

    fields = extract_form_fields(filepath)
    session["form_fields"] = fields

    return redirect(url_for("index"))

# PDF-Vorschau anzeigen
@app.route("/uploaded/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# Chat API: fragt nach leeren Feldern
@app.route("/api/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "Keine Nachricht empfangen."}), 400

    filename = session.get("filename")
    field_state = session.get("field_state", {"_asked": [], "_answered": {}})
    form_fields = session.get("form_fields", {})

    # Alle leeren Felder, die noch nicht beantwortet wurden
    unanswered_fields = [f for f, v in form_fields.items() if not v and f not in field_state["_answered"]]

    # Wenn ein Feld gerade beantwortet wird
    if field_state["_asked"]:
        last_field = field_state["_asked"][-1]
        field_state["_answered"][last_field] = user_message
        field_state["_asked"] = []

    # Nächstes leeres Feld bestimmen
    next_field = next((f for f in unanswered_fields if f not in field_state["_asked"]), None)

    if next_field:
        field_state["_asked"].append(next_field)
        session["field_state"] = field_state
        return jsonify({"reply": f"Was soll in das Feld '{next_field}' eingetragen werden?"})

    # Wenn alles beantwortet ist
    session["field_state"] = field_state
    return jsonify({"reply": "Alle Felder wurden ausgefüllt. Du kannst jetzt auf 'Exportieren' klicken."})

# PDF exportieren mit ausgefüllten Feldern
@app.route("/export", methods=["GET"])
def export_pdf():
    filename = session.get("filename")
    answers = session.get("field_state", {}).get("_answered", {})
    if not filename or not answers:
        return "Keine Daten zum Exportieren", 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    reader = PdfReader(filepath)
    writer = PdfWriter()

    # Alle Seiten übernehmen und Felder aktualisieren
    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        writer.update_page_form_field_values(writer.pages[i], answers)

    # Sichtbarkeit sicherstellen
    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): reader.trailer["/Root"]["/AcroForm"]["/Fields"],
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        })

    # Neue Datei im Speicher schreiben
    output = io.BytesIO()
    writer.write(output)
    output.seek(0)

    export_filename = f"ausgefuellt_{filename}"
    export_path = os.path.join(app.config["UPLOAD_FOLDER"], export_filename)

    with open(export_path, "wb") as f:
        f.write(output.read())

    return send_from_directory(
        directory=app.config["UPLOAD_FOLDER"],
        path=export_filename,
        as_attachment=True,
        download_name=export_filename,
        mimetype='application/pdf'
    )

# App starten
if __name__ == "__main__":
    app.run(debug=True)
