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

# ---- User Auth: Supabase DB ----

def hash_password(password):
    return bcrypt.hashpw(password.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

def verify_password(password, hashed):
    return bcrypt.checkpw(password.encode('utf-8'), hashed.encode('utf-8'))

# ---- PDF Logic (same as before) ----

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
    filename = session.pop("filename", None)
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

@app.route("/remove", methods=["POST"])
def remove_pdf():
    session.pop("filename", None)
    session.pop("form_fields", None)
    session.pop("field_state", None)
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

# ---- Supabase Signup/Login ----

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "")
        lastname = request.form.get("lastname", "")
        email = request.form.get("email")
        password = request.form.get("password")

        # Check if email exists
        result = supabase.table("signup").select("id").eq("email", email).execute()
        if result.data:
            flash("E-Mail existiert bereits!")
            return redirect(url_for("signup"))

        hashed_password = hash_password(password)
        # Insert into signup table
        supabase.table("signup").insert({
            "name": name,
            "lastname": lastname,
            "email": email,
            "password": hashed_password
        }).execute()
        flash("Registrierung erfolgreich! Bitte loggen Sie sich ein.")
        return redirect(url_for("login"))

    return render_template("signup.html")

@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        email = request.form.get("email")
        password = request.form.get("password")

        # Fetch user by email
        res = supabase.table("signup").select("*").eq("email", email).execute()
        user = res.data[0] if res.data else None

        success = False
        if user and verify_password(password, user["password"]):
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            flash("Login erfolgreich!")
            success = True
            redirect_url = url_for("main")
        else:
            flash("Login fehlgeschlagen.")
            redirect_url = url_for("login")

        # Log attempt in login table
        supabase.table("login").insert({
            "email": email,
            "password": user["password"] if user else "",
            "login_date": datetime.utcnow().isoformat(),
            "success": success
        }).execute()

        return redirect(redirect_url)

    return render_template("login.html")

@app.route("/logout")
def logout():
    session.clear()
    flash("Erfolgreich ausgeloggt.")
    return redirect(url_for("login"))

# --- Profile logic stays as before, or adapt to Supabase as needed ---

@app.route("/profil", methods=["GET", "POST"])
def profil():
    user_email = session.get("user_email")
    if not user_email:
        flash("Bitte loggen Sie sich zuerst ein.")
        return redirect(url_for("login"))

    # Example: Fetch current profile from Supabase
    res = supabase.table("signup").select("*").eq("email", user_email).execute()
    user = res.data[0] if res.data else {}

    if request.method == "POST":
        name = request.form.get("name")
        email = request.form.get("email")
        nachname = request.form.get("nachname")
        # ...other fields if you add them...
        # Update user info in Supabase
        supabase.table("signup").update({
            "name": name,
            "lastname": nachname,
            "email": email
        }).eq("email", user_email).execute()

        session["user_email"] = email  # Update session if email changed
        flash("Profil erfolgreich aktualisiert!")
        return redirect(url_for("profil"))

    return render_template("profil.html", user=user)

@app.route('/main')
def main():
    if "user_id" not in session:
        return redirect(url_for("login"))
    return render_template('main.html')

@app.route('/interface')
def interface():
    return render_template('interface.html')

if __name__ == "__main__":
    app.run(debug=True)

@app.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = request.form.get("name", "").strip()
        lastname = request.form.get("lastname", "").strip()
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Validate input
        if not name or not lastname or not email or not password:
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
            "name": name,
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
        email = request.form.get("email", "").strip().lower()
        password = request.form.get("password", "")

        # Fetch user by email
        res = supabase.table("signup").select("*").eq("email", email).execute()
        user = res.data[0] if res.data else None

        success = False
        if user and verify_password(password, user["password"]):
            # Correct password
            session["user_id"] = user["id"]
            session["user_email"] = user["email"]
            flash("Login erfolgreich!")
            success = True
            redirect_url = url_for("main")
        else:
            # Wrong credentials
            flash("E-Mail oder Passwort falsch.")
            redirect_url = url_for("login")

        # Log every attempt (success/fail)
        supabase.table("login").insert({
            "email": email,
            "password": user["password"] if user else "",
            "login_date": datetime.utcnow().isoformat(),
            "success": success
        }).execute()

        return redirect(redirect_url)

    # GET: Render the login form
    return render_template("login.html")

