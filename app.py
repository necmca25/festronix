from flask import Flask, request, jsonify, send_from_directory, render_template
from flask_cors import CORS
import sqlite3, os, jwt, smtplib, json
from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from email.message import EmailMessage

app = Flask(__name__)
CORS(app)

# ================= CONFIG =================
DB = 'festronix.db'
SECRET_KEY = 'festronix-secret'
UPLOAD_FOLDER = 'uploads'

GMAIL_USER = os.getenv('GMAIL_USER')
GMAIL_APP_PASSWORD = os.getenv('GMAIL_APP_PASSWORD')


ALLOWED_EXTENSIONS = {'png','jpg','jpeg','gif','bmp','pdf','ppt','pptx'}

if not os.path.exists(UPLOAD_FOLDER):
    os.makedirs(UPLOAD_FOLDER)

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ================= DB =================
def get_db():
    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    db = get_db()
    db.execute('''
        CREATE TABLE IF NOT EXISTS admin(
            id INTEGER PRIMARY KEY,
            username TEXT,
            password TEXT
        )
    ''')
    db.execute('''
        CREATE TABLE IF NOT EXISTS registrations(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            regno TEXT,
            email TEXT,
            mobile TEXT,
            college TEXT,
            dept TEXT,
            year TEXT,
            event TEXT,
            team_members TEXT,
            ppt_file TEXT,
            status TEXT DEFAULT 'Pending',
            created_at TEXT
        )
    ''')
    # Default admin
    admin = db.execute("SELECT * FROM admin").fetchone()
    if not admin:
        db.execute("INSERT INTO admin (username,password) VALUES (?,?)",
                   ('admin', generate_password_hash('admin123')))
    db.commit()
    db.close()

init_db()

# ================= AUTH =================
def token_required(f):
    def wrap(*args, **kwargs):
        token = request.headers.get('Authorization')
        if not token:
            return jsonify({'error':'Token missing'}),401
        try:
            jwt.decode(token, SECRET_KEY, algorithms=['HS256'])
        except:
            return jsonify({'error':'Invalid token'}),401
        return f(*args, **kwargs)
    wrap.__name__ = f.__name__
    return wrap

# ================= ROUTES =================
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/register')
def register_page():
    return render_template('register.html')

@app.route('/admin')
def admin_page():
    return render_template('admin.html')
@app.route('/events')
def events_page():
    return render_template('events.html')




@app.route('/event/code-relay')
def code_relay():
    return render_template('code-relay.html')


@app.route('/event/tech-trio')
def tech_trio():
    return render_template('tech-trio.html')


@app.route('/event/pp')
def pp():
    return render_template('pp.html')


@app.route('/event/adzap')
def adzap():
    return render_template('adzap.html')


@app.route('/event/fp')
def fp():
    return render_template('fp.html')


# ================= ADMIN LOGIN =================
@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    data = request.json
    db = get_db()
    admin = db.execute("SELECT * FROM admin WHERE username=?", (data['username'],)).fetchone()
    db.close()
    if admin and check_password_hash(admin['password'], data['password']):
        token = jwt.encode({'user':'admin','exp':datetime.utcnow()+timedelta(hours=6)},
                           SECRET_KEY, algorithm='HS256')
        return jsonify({'token':token})
    return jsonify({'error':'Invalid login'}),401

# ================= REGISTER =================
@app.route('/api/register', methods=['POST'])
def register():
    try:
        ppt_file = request.files.get('pptFile')
        ppt_filename = None

        if ppt_file and ppt_file.filename and allowed_file(ppt_file.filename):
            ppt_filename = secure_filename(f"{datetime.now().strftime('%Y%m%d%H%M%S')}_{ppt_file.filename}")
            ppt_file.save(os.path.join(app.config['UPLOAD_FOLDER'], ppt_filename))

        team_members = request.form.get('teamMembers')

        db = get_db()
        # Duplicate check by regno or email
        existing = db.execute("SELECT * FROM registrations WHERE regno=? OR email=?",
                              (request.form.get('regno'), request.form.get('email'))).fetchone()
        if existing:
            return jsonify({'error': 'User already registered'}), 400

        db.execute('''
            INSERT INTO registrations
            (name, regno, email, mobile, college, dept, year, event, team_members, ppt_file, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ''',(
            request.form.get('name'),
            request.form.get('regno'),
            request.form.get('email'),
            request.form.get('mobile'),
            request.form.get('college'),
            request.form.get('dept'),
            request.form.get('year'),
            request.form.get('event'),
            team_members,
            ppt_filename,
            datetime.now().isoformat()
        ))
        db.commit()
        db.close()

        # Send confirmation email immediately
        send_email(request.form.get('email'), request.form.get('name'),
                   event=request.form.get('event'),
                   team_members=json.loads(team_members) if team_members else None,
                   regno=request.form.get('regno'))

        return jsonify({'success': True})
    except Exception as e:
        print(f"Registration error: {e}")
        return jsonify({'error': str(e)}),500

# ================= GET REGISTRATIONS =================
@app.route('/api/registrations')
@token_required
def get_regs():
    db = get_db()
    rows = db.execute("SELECT * FROM registrations ORDER BY id DESC").fetchall()
    db.close()
    return jsonify([dict(r) for r in rows])

# ================= UPDATE STATUS =================
@app.route('/api/registration/<int:id>/status', methods=['PUT'])
@token_required
def update_status(id):
    status = request.json['status']
    db = get_db()
    row = db.execute("SELECT * FROM registrations WHERE id=?", (id,)).fetchone()
    db.execute("UPDATE registrations SET status=? WHERE id=?", (status, id))
    db.commit()
    db.close()

    if status == 'Approved' and row:
        # Get team members list from DB
        team_members = []
        if row['team_members']:
            team_members = json.loads(row['team_members'])
        send_email(
            row['email'],
            row['name'],
            event=row['event'],
            team_members=team_members,
            regno=row['regno']
        )
    return jsonify({'success': True})

# ================= DELETE =================
@app.route('/api/registration/<int:id>', methods=['DELETE'])
@token_required
def delete_row(id):
    db = get_db()
    row = db.execute("SELECT ppt_file FROM registrations WHERE id=?", (id,)).fetchone()
    if row and row['ppt_file']:
        try:
            os.remove(os.path.join(app.config['UPLOAD_FOLDER'], row['ppt_file']))
        except:
            pass
    db.execute("DELETE FROM registrations WHERE id=?", (id,))
    db.commit()
    db.close()
    return jsonify({'success':True})

# ================= FILE SERVE =================
@app.route('/uploads/<path:filename>')
def uploads(filename):
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

# ================= EMAIL =================
def send_email(to, name, event=None, team_members=None, regno=None):
    try:
        msg = EmailMessage()
        msg['Subject'] = f'FESTRONIX Registration Approved ✅'
        msg['From'] = GMAIL_USER
        msg['To'] = to

        team_info = ', '.join(team_members) if team_members else 'Solo'
        reg_info = f"Registration Number: {regno}" if regno else ""

        msg.set_content(f"""
Hi {name},

🎉 Congratulations! Your registration for FESTRONIX '26 has been approved!

Event Details:
- Event: {event if event else 'N/A'}
- Team Members: {team_info}
- {reg_info}

Please make sure to:
- Carry a valid college ID on the event day
- Arrive on time

We look forward to seeing you at FESTRONIX '26!

Best regards,
Team FESTRONIX
        """)

        with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
            s.login(GMAIL_USER, GMAIL_APP_PASSWORD)
            s.send_message(msg)

        print(f"Email sent successfully to {to}")

    except Exception as e:
        print(f"Email error: {e}")

# ================= RUN =================
# ================= RUN =================
if __name__ == '__main__':
    app.run()

