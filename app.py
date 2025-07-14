import os
import io
import json
from datetime import datetime, timezone
from functools import wraps
from flask import (
    Flask, request, render_template, redirect, url_for,
    send_from_directory, session, jsonify, flash
)
from werkzeug.utils import secure_filename
from pypdf import PdfReader, PdfWriter
from pypdf.generic import NameObject, DictionaryObject, BooleanObject
from dotenv import load_dotenv
from gemini_client import get_gemini_response
from supabase import create_client, Client
import bcrypt

load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

# Login erforderlich für bestimmte Routen
def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Bitte logge dich ein.")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view

# Passwort-Hashing mit bcrypt
def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# Formulardaten aus PDF nach Reihenfolge der Positionen extrahieren
def extract_form_fields_ordered_by_position(pdf_path):
    reader = PdfReader(pdf_path)
    field_data = []
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
    sorted_fields = sorted(field_data, key=lambda x: (x[0], x[1], x[2]))
    field_order = [name for _, _, _, name in sorted_fields]
    return field_order

# Positionen aller Formularfelder extrahieren
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

# Formularfelder und Labels aus PDF extrahieren
def extract_form_fields(pdf_path):
    reader = PdfReader(pdf_path)
    fields = {}
    labels = {}
    if reader.get_fields():
        for field in reader.get_fields().values():
            name = field.get('/T')
            value = field.get('/V', '')
            tooltip = field.get('/TU', '')
            if name:
                fields[name] = value or ''
                labels[name] = tooltip if tooltip else name
    return fields, labels

# Interface-Seite anzeigen
@app.route("/interface")
@login_required
def interface():
    filename = session.get("filename")
    return render_template(
        'interface.html',
        filename=filename,
        labels=session.get("field_labels", {}),
        positions=session.get("field_positions", {}),
        values=session.get("field_state", {}).get("_answered", {})
    )

# PDF-Upload und Formulardaten-Initialisierung
@app.route("/upload", methods=["POST"])
def upload_pdf():
    import re

    def prettify_field_name(name):
        name = re.sub(r"[_\-]+", " ", name)
        name = re.sub(r"([a-z])([A-Z])", r"\1 \2", name)
        return name.strip().capitalize()

    if "pdf_file" not in request.files:
        return "Keine Datei hochgeladen", 400

    file = request.files["pdf_file"]
    if file.filename == "":
        return "Leerer Dateiname", 400

    filename = secure_filename(file.filename)
    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    file.save(filepath)
    session.pop("field_state", None)
    session["filename"] = filename
    session["field_state"] = {"_asked": [], "_answered": {}}
    field_order = extract_form_fields_ordered_by_position(filepath)
    session["field_order"] = field_order
    field_positions = extract_form_fields_positions(filepath)
    session["field_positions"] = field_positions
    fields, _ = extract_form_fields(filepath)
    session["form_fields"] = fields
    session["field_labels"] = {name: prettify_field_name(name) for name in field_order}
    session["field_state"]["_answered"] = {}
    return redirect(url_for("interface"))

# PDF aus Session entfernen
@app.route("/remove", methods=["POST"])
def remove_pdf():
    session.pop("filename", None)
    session.pop("form_fields", None)
    session.pop("field_state", None)
    return redirect(url_for("interface"))

# PDF bereitstellen
@app.route("/uploaded/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

# Prüft, ob ein gültiges PDF hochgeladen wurde
def is_valid_pdf_uploaded():
    return "filename" in session and "form_fields" in session and bool(session["form_fields"])

# Haupt-Chat-API für Formulardialog und Validierung
@app.route("/api/chat", methods=["POST"])
def chat():
    user_message = request.json.get("message")
    if not user_message:
        return jsonify({"reply": "Keine Nachricht empfangen."}), 400

    form_fields = session.get("form_fields", {})
    field_state = session.get("field_state", {"_asked": [], "_answered": {}})
    field_order = session.get("field_order", list(form_fields.keys()))
    field_labels = session.get("field_labels", {})
    unanswered_fields = [f for f in field_order if f not in field_state["_answered"]]

    # Begrüßung ohne PDF
    if user_message.lower() == "init" and not ("filename" in session and form_fields):
        return jsonify({
            "reply": (
                "Hallo! Ich bin der FormFillBot und helfe dir beim Ausfüllen deines PDF-Formulars.\n"
                "Bitte lade zuerst ein PDF-Formular hoch."
            )
        })

    # Begrüßung mit PDF → erste Frage
    if user_message.lower() == "init":
        if not unanswered_fields:
            return jsonify({"reply": "Das Formular ist bereits vollständig ausgefüllt."})
        first_field = unanswered_fields[0]
        label = field_labels.get(first_field, first_field)
        filename = session.get("filename", "Formular").replace(".pdf", "")
        field_state["_asked"] = [first_field]
        session["field_state"] = field_state
        return jsonify({
            "reply": f"Ich werde dir dabei helfen, dein {filename}-Formular auszufüllen. Wie lautet dein Wert für „{label}“?"
        })

    updated_fields = {}
    if field_state["_asked"]:
        last_field = field_state["_asked"][-1]
        user_input = user_message.strip()
        label = field_labels.get(last_field, last_field)
        remaining = [f for f in field_order if f not in field_state["_answered"] and f != last_field]
        next_field = remaining[0] if remaining else None
        next_label = field_labels.get(next_field, next_field) if next_field else ""

        # Validierung mit Gemini
        validation_prompt = f"""
Du bist ein intelligenter Formularassistent.

Der Nutzer hat für das Feld „{label}“ folgenden Wert eingegeben:
„{user_input}“

Prüfe, ob die Eingabe gültig ist.

Regeln:
- Für E-Mail: Muss eine vollständige Adresse sein mit @ und Domainendung wie .de oder .com.
- Für Telefonnummer: Gültig sind realistische Formate wie „0234 1234567“ oder mobil „+49 176 12345678“.
- Für Namen/Firma/etc.: Sollte plausibel und sinnvoll klingen.

Wenn die Eingabe **ungültig** ist, antworte mit einer freundlichen Rückfrage, z. B.:
„Hoppla, deine Telefonnummer scheint unvollständig zu sein. Bitte gib sie im Format 0234 1234567 oder +49 176 12345678 ein.“

Wenn die Eingabe **gültig** ist, frage explizit:
„Wie lautet dein Wert für „{next_label}“?“

Verwende die Du-Form. Antworte nur mit Rückfrage oder nächster Frage.
"""
        response = get_gemini_response(validation_prompt).strip()

        # Rückfrage? → nicht speichern
        if any(word in response.lower() for word in ["ungültig", "unvollständig", "falsch", "format", "vervollständig", "korrektur"]):
            return jsonify({
                "reply": response,
                "updated_fields": {}
            })

        # Falls Gemini nicht zur nächsten Frage überleitet
        if next_field and next_label.lower() not in response.lower():
            return jsonify({
                "reply": f"Deine Antwort wurde verarbeitet. Bitte gib deinen Wert für „{next_label}“ an.",
                "updated_fields": {}
            })

        # Eingabe gültig → speichern
        field_state["_answered"][last_field] = user_input
        updated_fields[last_field] = user_input
        field_state["_asked"] = []

        if next_field:
            field_state["_asked"] = [next_field]
            session["field_state"] = field_state
            return jsonify({
                "reply": response,
                "updated_fields": updated_fields
            })
        else:
            session["field_state"] = field_state
            return jsonify({
                "reply": "Alle Felder wurden ausgefüllt. Du kannst jetzt auf 'Exportieren' klicken.",
                "updated_fields": updated_fields
            })

    return jsonify({
        "reply": "Alle Felder wurden bereits ausgefüllt.",
        "updated_fields": {}
    })

# Exportiert das ausgefüllte PDF
@app.route("/export", methods=["GET"])
def export_pdf():
    filename = session.get("filename")
    answers = session.get("field_state", {}).get("_answered", {})

    if not filename or not answers:
        return "Keine Daten zum Exportieren", 400

    filepath = os.path.join(app.config["UPLOAD_FOLDER"], filename)
    reader = PdfReader(filepath)
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    if "/AcroForm" in reader.trailer["/Root"]:
        acroform = reader.trailer["/Root"]["/AcroForm"]
        writer._root_object.update({
            NameObject("/AcroForm"): acroform.get_object()
        })
        writer.update_page_form_field_values(writer.pages[0], answers)
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

# Benutzer abmelden
@app.route("/logout")
def logout():
    session.clear()
    flash("Erfolgreich ausgeloggt.")
    return redirect(url_for("main"))

# Session-Status für Debugging ausgeben
@app.before_request
def print_session_state():
    print("Aktuelle Session:", dict(session))

# Profil-Anzeige und -Bearbeitung
@app.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    user_email = session.get("user_email")
    res = supabase.table("signup").select("*").eq("email", user_email).execute()
    user = res.data[0] if res.data else {}
    if request.method == "POST":
        vorname = request.form.get("name")
        email = request.form.get("email")
        nachname = request.form.get("nachname")
        supabase.table("signup").update({
            "vorname": vorname,
            "lastname": nachname,
            "email": email
        }).eq("email", user_email).execute()
        session["user_email"] = email
        flash("Profil erfolgreich aktualisiert!")
        return redirect(url_for("profil"))
    return render_template("profil.html", user=user)

# Main-Page
@app.route('/main')
def main():
    return render_template('main.html')

# Registrierung neuer Benutzer
@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        vorname = request.form.get("vorname", "").strip()
        lastname = request.form.get("lastname", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")
        if not vorname or not lastname or not email or not password:
            flash("Bitte alle Felder ausfüllen.")
            return redirect(url_for("signup"))
        result = supabase.table("signup").select("id").eq("email", email).execute()
        if result.data:
            flash("E-Mail existiert bereits!")
            return redirect(url_for("signup"))
        hashed_password = hash_password(password)
        supabase.table("signup").insert({
            "vorname": vorname,
            "lastname": lastname,
            "email": email,
            "password": hashed_password
        }).execute()
        flash("Registrierung erfolgreich! Bitte loggen Sie sich ein.")
        return redirect(url_for("login"))
    return render_template("signup.html")

# Login-Funktion
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form["email"]
        password = request.form["password"]
        user_query = supabase.table("signup").select("id, password").eq("email", email).execute()
        user_data = user_query.data[0] if user_query.data else None
        success = False
        redirect_url = url_for("login")
        if user_data and user_data.get("password"):
            try:
                if verify_password(password, user_data["password"]):
                    session["user_id"] = user_data["id"]
                    session["user_email"] = email
                    success = True
                    redirect_url = url_for("interface")
                    flash("Login erfolgreich!")
                else:
                    flash("Falsches Passwort.")
            except ValueError:
                flash("Ungültiger Passwort-Hash.")
        else:
            flash("Benutzer nicht gefunden.")
        supabase.table("login").insert({
            "user_id": user_data["id"] if user_data else None,
            "email": email,
            "password": user_data["password"] if user_data else "",
            "login_date": datetime.now(timezone.utc).isoformat(),
            "success": success
        }).execute()
        return redirect(redirect_url)
    return render_template("login.html")

# Start der Flask-App
if __name__ == "__main__":
    print("Starte Flask-App")
    app.run(debug=True)