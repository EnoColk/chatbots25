import os
import io
import json
from flask import (
    Flask, request, render_template, redirect, url_for,
    send_from_directory, session, jsonify, flash
)
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject, DictionaryObject, BooleanObject
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

USER_DATA_FILE = "user_data.json"

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_user_data(data):
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f)

def extract_form_fields(pdf_path):
    reader = PdfReader(pdf_path)
    fields = {}
    if reader.get_fields():
        for field in reader.get_fields().values():
            name = field.get('/T')
            value = field.get('/V', '')
            if name:
                fields[name] = value or ''
    return fields

@app.route("/", methods=["GET"])
def index():
    filename = session.get("filename")
    return render_template("interface.html", filename=filename)

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

@app.route("/uploaded/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/api/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "Keine Nachricht empfangen."}), 400

    filename = session.get("filename")
    field_state = session.get("field_state", {"_asked": [], "_answered": {}})
    form_fields = session.get("form_fields", {})

    unanswered_fields = [f for f, v in form_fields.items() if not v and f not in field_state["_answered"]]

    if field_state["_asked"]:
        last_field = field_state["_asked"][-1]
        field_state["_answered"][last_field] = user_message
        field_state["_asked"] = []

    next_field = next((f for f in unanswered_fields if f not in field_state["_asked"]), None)

    if next_field:
        field_state["_asked"].append(next_field)
        session["field_state"] = field_state
        return jsonify({"reply": f"Was soll in das Feld '{next_field}' eingetragen werden?"})

    session["field_state"] = field_state
    return jsonify({"reply": "Alle Felder wurden ausgefüllt. Du kannst jetzt auf 'Exportieren' klicken."})

@app.route("/export", methods=["GET"])
def export_pdf():
    filename = session.get("filename")
    answers = session.get("field_state", {}).get("_answered", {})
    if not filename or not answers:
        return "Keine Daten zum Exportieren", 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    reader = PdfReader(filepath)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        writer.update_page_form_field_values(writer.pages[i], answers)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): reader.trailer["/Root"]["/AcroForm"]["/Fields"],
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        })

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

@app.route("/profil", methods=["GET", "POST"])
def profil():
    # Kein Redirect, einfach Default-Email verwenden falls nicht gesetzt
    user_email = session.get("user_email", "default@example.com")
    all_users = load_user_data()
    user = all_users.get(user_email, {})

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        nachname = request.form.get("nachname")
        adresse = request.form.get("adresse")
        telefon = request.form.get("telefon")
        familienstand = request.form.get("familienstand")
        geschlecht = request.form.get("geschlecht")
        land = request.form.get("land")

        user.update({
            "name": name,
            "email": email,
            "nachname": nachname,
            "adresse": adresse,
            "telefon": telefon,
            "familienstand": familienstand,
            "geschlecht": geschlecht,
            "land": land,
        })

        all_users[user_email] = user
        save_user_data(all_users)
        flash("Profil erfolgreich aktualisiert!")

        return redirect(url_for("profil"))

    return render_template("profil.html", user=user)

import os
import io
import json
from flask import (
    Flask, request, render_template, redirect, url_for,
    send_from_directory, session, jsonify, flash
)
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader, PdfWriter
from PyPDF2.generic import NameObject, DictionaryObject, BooleanObject
from dotenv import load_dotenv

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

USER_DATA_FILE = "user_data.json"

def load_user_data():
    if os.path.exists(USER_DATA_FILE):
        with open(USER_DATA_FILE, "r") as f:
            return json.load(f)
    return {}

def save_user_data(data):
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f)

def extract_form_fields(pdf_path):
    reader = PdfReader(pdf_path)
    fields = {}
    if reader.get_fields():
        for field in reader.get_fields().values():
            name = field.get('/T')
            value = field.get('/V', '')
            if name:
                fields[name] = value or ''
    return fields

@app.route("/", methods=["GET"])
def index():
    filename = session.get("filename")
    return render_template("interface.html", filename=filename)

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

@app.route("/uploaded/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/api/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "Keine Nachricht empfangen."}), 400

    filename = session.get("filename")
    field_state = session.get("field_state", {"_asked": [], "_answered": {}})
    form_fields = session.get("form_fields", {})

    unanswered_fields = [f for f, v in form_fields.items() if not v and f not in field_state["_answered"]]

    if field_state["_asked"]:
        last_field = field_state["_asked"][-1]
        field_state["_answered"][last_field] = user_message
        field_state["_asked"] = []

    next_field = next((f for f in unanswered_fields if f not in field_state["_asked"]), None)

    if next_field:
        field_state["_asked"].append(next_field)
        session["field_state"] = field_state
        return jsonify({"reply": f"Was soll in das Feld '{next_field}' eingetragen werden?"})

    session["field_state"] = field_state
    return jsonify({"reply": "Alle Felder wurden ausgefüllt. Du kannst jetzt auf 'Exportieren' klicken."})

@app.route("/export", methods=["GET"])
def export_pdf():
    filename = session.get("filename")
    answers = session.get("field_state", {}).get("_answered", {})
    if not filename or not answers:
        return "Keine Daten zum Exportieren", 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    reader = PdfReader(filepath)
    writer = PdfWriter()

    for i, page in enumerate(reader.pages):
        writer.add_page(page)
        writer.update_page_form_field_values(writer.pages[i], answers)

    if "/AcroForm" in reader.trailer["/Root"]:
        writer._root_object.update({
            NameObject("/AcroForm"): DictionaryObject({
                NameObject("/Fields"): reader.trailer["/Root"]["/AcroForm"]["/Fields"],
                NameObject("/NeedAppearances"): BooleanObject(True)
            })
        })

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

@app.route("/profil", methods=["GET", "POST"])
def profil():
    # Kein Redirect, einfach Default-Email verwenden falls nicht gesetzt
    user_email = session.get("user_email", "default@example.com")
    all_users = load_user_data()
    user = all_users.get(user_email, {})

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        nachname = request.form.get("nachname")
        adresse = request.form.get("adresse")
        telefon = request.form.get("telefon")
        familienstand = request.form.get("familienstand")
        geschlecht = request.form.get("geschlecht")
        land = request.form.get("land")

        user.update({
            "name": name,
            "email": email,
            "nachname": nachname,
            "adresse": adresse,
            "telefon": telefon,
            "familienstand": familienstand,
            "geschlecht": geschlecht,
            "land": land,
        })

        all_users[user_email] = user
        save_user_data(all_users)
        flash("Profil erfolgreich aktualisiert!")

        return redirect(url_for("profil"))

    return render_template("profil.html", user=user)

@app.route('/main')
def main():
    return render_template('main.html')

@app.route('/interface')
def interface():
    return render_template('interface.html')

@app.route('/landing')
def landing():
    return render_template('landing.html')

if __name__ == "__main__":
    app.run(debug=True)

if __name__ == "__main__":
    app.run(debug=True)
