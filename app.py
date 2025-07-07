import os
import io
import json
from flask import (
    Flask, request, render_template, redirect, url_for,
    send_from_directory, session, jsonify, flash
)
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject, BooleanObject
from dotenv import load_dotenv
from gemini_client import get_gemini_response  # 💡 Wichtig: Gemini-Client importieren

# .env laden
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

def is_valid_pdf_uploaded():
    filename = session.get("filename")
    form_fields = session.get("form_fields", {})
    return bool(filename and isinstance(form_fields, dict) and len(form_fields) > 0)


def save_user_data(data):
    with open(USER_DATA_FILE, "w") as f:
        json.dump(data, f)

def extract_form_fields_ordered_by_position(pdf_path):
    reader = PdfReader(pdf_path)
    field_data = []
    fields = {}

    for page_index, page in enumerate(reader.pages):
        annotations = page.get("/Annots", [])
        for annot in annotations:
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Widget" and "/T" in obj:
                name = obj["/T"]
                rect = obj.get("/Rect", [0, 0, 0, 0])
                if len(rect) == 4:
                    llx, lly, urx, ury = map(float, rect)
                    x_center = (llx + urx) / 2
                    y_center = (lly + ury) / 2
                    field_data.append((page_index, -y_center, x_center, name))
    # Sortieren: Seite, y-Achse (von oben nach unten), x-Achse (von links nach rechts)
    sorted_fields = sorted(field_data, key=lambda x: (x[0], x[1], x[2]))
    field_order = [name for _, _, _, name in sorted_fields]
    return field_order

def extract_form_fields_positions(pdf_path):
    reader = PdfReader(pdf_path)
    positions = {}
    for page_idx, page in enumerate(reader.pages):
        annots = page.get("/Annots", [])
        for annot in annots:
            obj = annot.get_object()
            if obj.get("/Subtype") == "/Widget" and "/T" in obj:
                name = obj["/T"]
                rect = obj.get("/Rect", [0, 0, 0, 0])
                if len(rect) == 4:
                    llx, lly, urx, ury = map(float, rect)
                    width = urx - llx
                    height = ury - lly
                    positions[name] = {
                        "page": page_idx,
                        "x": llx,
                        "y": lly,
                        "width": width,
                        "height": height
                    }
    return positions





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
    filename = session.get("filename")  # ✅ Nur lesen, nicht löschen!
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

    # ⛔️ Vorherige Formular-Zustände entfernen
    session.pop("field_state", None)

    # 📥 Neue Datei + leeren Zustand setzen
    session["filename"] = filename
    session["field_state"] = {"_asked": [], "_answered": {}}

    # 🧾 Formularfelder extrahieren (alle Felder)
    fields = extract_form_fields(filepath)
    session["form_fields"] = fields

    # 🆕 Reihenfolge extrahieren und speichern!
    field_order = extract_form_fields_ordered_by_position(filepath)
    session["field_order"] = field_order

    # 🆕 Positionen extrahieren und speichern!
    field_positions = extract_form_fields_positions(filepath)
    session["field_positions"] = field_positions

    return redirect(url_for("index"))


@app.route("/remove", methods=["POST"])
def remove_pdf():
    session.pop("filename", None)
    session.pop("form_fields", None)
    session.pop("field_state", None)
    return redirect(url_for("index"))

@app.route("/uploaded/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# 🔍 Prüft, ob ein gültiges PDF mit Formularfeldern hochgeladen wurde
def is_valid_pdf_uploaded():
    return "filename" in session and "form_fields" in session and bool(session["form_fields"])

@app.route("/api/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "Keine Nachricht empfangen."}), 400

    # 🧠 Formularstatus aus der Session laden
    form_fields = session.get("form_fields", {})
    field_state = session.get("field_state", {"_asked": [], "_answered": {}})
    field_order = session.get("field_order", list(form_fields.keys()))  # ✅ So ist es richtig!
    unanswered_fields = [f for f in field_order if f not in field_state["_answered"]]

    # 🟣 Fall 1: Begrüßung ohne PDF – Hinweis
    if user_message.lower() == "init" and not is_valid_pdf_uploaded():
        prompt = """
Du bist der FormFillBot, ein intelligenter PDF-Assistent. Begrüße den Nutzer mit diesem Satz:

„Hallo! Ich bin der FormFillBot und helfe dir beim Ausfüllen deines PDF-Formulars.“

Füge dann hinzu:

„Bitte lade zunächst ein PDF-Formular hoch, damit ich dir helfen kann.“

Sprich freundlich und klar. Verwende exakt diese zwei Sätze.
"""
        return jsonify({"reply": get_gemini_response(prompt)})

    # 🟣 Fall 2: Begrüßung + erste Feldfrage mit dynamischem Formularnamen
    if user_message.lower() == "init":
        if not unanswered_fields:
            return jsonify({"reply": "Das Formular ist bereits vollständig ausgefüllt."})

        first_field = unanswered_fields[0]
        raw_filename = session.get("filename", "")
        formularname = raw_filename.rsplit("/", 1)[-1].replace(".pdf", "").strip()

        prompt = f"""
Du bist der FormFillBot, ein intelligenter Assistent, der PDF-Formulare ausfüllt.

Begrüße den Nutzer mit:

„Ich werde dir dabei helfen, dein {formularname} auszufüllen.“

Stelle danach sofort die erste Frage zum Feld „{first_field}“. Frage freundlich in Du-Form.

Formuliere beide Sätze gemeinsam in natürlicher Sprache. Antworte nur mit diesen zwei Sätzen.
"""
        reply = get_gemini_response(prompt)
        field_state["_asked"].append(first_field)
        session["field_state"] = field_state
        return jsonify({"reply": reply})

    # ✍️ Nutzerantwort speichern
    if field_state["_asked"]:
        last_field = field_state["_asked"][-1]
        field_state["_answered"][last_field] = user_message
        field_state["_asked"] = []

    # ⏭️ Nächstes offenes Feld abfragen
    unanswered_fields = [f for f in field_order if f not in field_state["_answered"]]
    next_field = next((f for f in unanswered_fields), None)

    if next_field:
        prompt = f"""
Formuliere eine freundliche Frage an den Nutzer, um das Feld „{next_field}“ im PDF-Formular auszufüllen.
Sprich in der Du-Form. Antworte nur mit der konkreten Frage.
"""
        reply = get_gemini_response(prompt)
        field_state["_asked"].append(next_field)
        session["field_state"] = field_state
        return jsonify({"reply": reply})

    # ✅ Alles beantwortet
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



