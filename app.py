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
from gemini_client import get_gemini_response  # 💡 Wichtig: Gemini-Client importieren

# Supabase and password hashing imports
from supabase import create_client, Client
import bcrypt
from datetime import datetime

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

app = Flask(__name__)
app.secret_key = "supersecretkey"
app.config["UPLOAD_FOLDER"] = "uploads"
os.makedirs(app.config["UPLOAD_FOLDER"], exist_ok=True)

def login_required(view_func):
    @wraps(view_func)
    def wrapped_view(*args, **kwargs):
        if "user_id" not in session:
            flash("Bitte logge dich ein.")
            return redirect(url_for("login"))
        return view_func(*args, **kwargs)
    return wrapped_view

# ---- User Auth: Supabase DB ----

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ---- PDF Logic (same as before) ----

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






@app.route("/interface")
@login_required
def interface():
    filename = session.get("filename")
    return render_template('interface.html', filename=filename)


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

    # Vorherige Formular-Zustände entfernen
    session.pop("field_state", None)

    # Neue Datei + leeren Zustand setzen
    session["filename"] = filename
    session["field_state"] = {"_asked": [], "_answered": {}}

    # Formularfelder extrahieren (alle Felder)
    fields = extract_form_fields(filepath)
    session["form_fields"] = fields

    # Reihenfolge extrahieren und speichern
    field_order = extract_form_fields_ordered_by_position(filepath)
    session["field_order"] = field_order

    # Positionen extrahieren und speichern
    field_positions = extract_form_fields_positions(filepath)
    session["field_positions"] = field_positions

    return redirect(url_for("interface"))


@app.route("/remove", methods=["POST"])
def remove_pdf():
    session.pop("filename", None)
    session.pop("form_fields", None)
    session.pop("field_state", None)
    return redirect(url_for("interface"))


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

@app.route("/logout")
def logout():
    session.clear()
    flash("Erfolgreich ausgeloggt.")
    return redirect(url_for("main"))

@app.before_request
def print_session_state():
    print("Aktuelle Session:", dict(session))


# --- Profile logic stays as before, or adapt to Supabase as needed ---

@app.route("/profil", methods=["GET", "POST"])
@login_required
def profil():
    user_email = session.get("user_email")

    # Aktuelles Profil holen
    res = supabase.table("signup").select("*").eq("email", user_email).execute()
    user = res.data[0] if res.data else {}

    if request.method == "POST":
        vorname = request.form.get("name")          # HTML-Feldname ist weiterhin "name"
        email = request.form.get("email")
        nachname = request.form.get("nachname")

        # Daten aktualisieren
        supabase.table("signup").update({
            "vorname": vorname,
            "lastname": nachname,
            "email": email
        }).eq("email", user_email).execute()

        session["user_email"] = email
        flash("Profil erfolgreich aktualisiert!")
        return redirect(url_for("profil"))  # ← MUSS innerhalb der if und der Funktion eingerückt sein!

    return render_template("profil.html", user=user)  # ← auch richtig eingerückt


@app.route('/main')
def main():
    return render_template('main.html')


@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        vorname = request.form.get("vorname", "").strip()
        lastname = request.form.get("lastname", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Validate input
        if not vorname or not lastname or not email or not password:
            flash("Bitte alle Felder ausfüllen.")
            return redirect(url_for("signup"))

        # Check if email already exists
        result = supabase.table("signup").select("id").eq("email", email).execute()
        if result.data:
            flash("E-Mail existiert bereits!")
            return redirect(url_for("signup"))

        # Hash the password
        hashed_password = hash_password(password)

        # Insert into signup table
        supabase.table("signup").insert({
            "vorname": vorname,
            "lastname": lastname,
            "email": email,
            "password": hashed_password
        }).execute()

        flash("Registrierung erfolgreich! Bitte loggen Sie sich ein.")
        return redirect(url_for("login"))

    # GET: Render the signup form
    return render_template("signup.html")

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
                    redirect_url = url_for("main")
                    flash("Login erfolgreich!")
                else:
                    flash("Falsches Passwort.")
            except ValueError:
                flash("Ungültiger Passwort-Hash.")
        else:
            flash("Benutzer nicht gefunden.")

        # Login-Versuch speichern
        supabase.table("login").insert({
            "user_id": user_data["id"] if user_data else None,
            "email": email,
            "password": user_data["password"] if user_data else "",
            "login_date": datetime.now(timezone.utc).isoformat(),
            "success": success
        }).execute()

        return redirect(redirect_url)

    return render_template("login.html")




    print("Starte Flask-App")
if __name__ == "__main__":
    app.run(debug=True)


