from flask import Blueprint, render_template, abort, session, request, redirect, flash, url_for, send_from_directory, jsonify, current_app
from functools import wraps
from flask_login import current_user
import os
import requests
import threading
import time
from datetime import datetime
from sqlalchemy import text
from werkzeug.utils import secure_filename
from core import db, socketio
from utils.utilities import text_extractor

# DB initialization for presentations
def init_presentation_db():
    create_table_query = """
    CREATE TABLE IF NOT EXISTS presentations (
        id VARCHAR(50) PRIMARY KEY,
        name VARCHAR(255) NOT NULL,
        teacher INTEGER NOT NULL,
        theme VARCHAR(50),
        subtopics TEXT,
        status VARCHAR(20) DEFAULT 'processing',
        date_created DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """
    try:
        db.session.execute(text(create_table_query))
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        print(f"SQL Error: {e}")
# Themes
THEME_CONFIG = {
    "clean_light":   {"file": "Clean-Light.html", "accent": "#4f46e5", "teal": "#06b6d4"},
    "clean_grid":    {"file": "Clean-Grid.html", "accent": "#4f46e5", "teal": "#06b6d4"},
    "grid_dark":     {"file": "Grid-Dark.html", "accent": "#0369a1", "teal": "#2dd4bf"},
    "minimal_light": {"file": "Minimal-Light.html", "accent": "#6366f1", "teal": "#f43f5e"},
    "neon_gray":     {"file": "Neon-Gray.html", "accent": "#2563eb", "teal": "#60a5fa"},
    "ocean_dark":    {"file": "Ocean-Dark.html", "accent": "#2563eb", "teal": "#60a5fa"},
    "puzzle_dark":   {"file": "Puzzle-Dark.html", "accent": "#2563eb", "teal": "#60a5fa"},
}


plugin_dir = os.path.dirname(os.path.abspath(__file__))

# records and uploads folders
records_folder = os.path.join(plugin_dir, 'records')
if not os.path.exists(records_folder):
    os.mkdir(records_folder)
uploads_folder = os.path.join(plugin_dir, '.uploads')
if not os.path.exists(uploads_folder):
    os.mkdir(uploads_folder)

# env file path
env_file = os.path.join(plugin_dir, '.env')
try:
    with open(env_file, "x") as f:
        pass 
except FileExistsError:
    print("File already exists, aborting to prevent data loss.")
except OSError as e:
    print(f"An error occurred while creating the file: {e}")


bp = Blueprint('presenta', __name__, template_folder='templates', root_path=plugin_dir, static_folder='static')


# admin and teacher wrappers
def admin_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.user_role != 'admin':
            abort(403) 
        return f(*args, **kwargs)
    return decorated_function

def teacher_only(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated or current_user.user_role != 'teacher':
            abort(403)
        return f(*args, **kwargs)
    return decorated_function

def generate_unique_id(prefix):
    import uuid
    return f"{prefix}_{str(uuid.uuid4()).split('-')[0].upper()}"


# Home route
@bp.route('/home/')
@teacher_only
def home():
    init_presentation_db()
    
    # raw SQL to get data for display
    active_sql = text("SELECT * FROM presentations WHERE teacher = :t AND status = 'processing' ORDER BY date_created DESC")
    prev_sql = text("SELECT * FROM presentations WHERE teacher = :t AND status = 'active' ORDER BY date_created DESC")
    
    active_data = db.session.execute(active_sql, {'t': session['id']}).fetchall()
    prev_rec = db.session.execute(prev_sql, {'t': session['id']}).fetchall()
    
    return render_template('presenta-home.html', data=prev_rec, active_data=active_data, record_folder = records_folder)

# report download route
@bp.route('/download/<report_id>/')
@teacher_only
def download_report(report_id):
    filename = f"{report_id}.html" 
    return send_from_directory(records_folder, filename, as_attachment=True)

# report delete route
@bp.route('/delete/<report_id>/')
@teacher_only
def delete_report(report_id):
    query = text("SELECT * FROM presentations WHERE id = :report_id LIMIT 1")
    report = db.session.execute(query, {"report_id": report_id}).fetchone()

    if not report:
        flash("Error: Report no longer exists.", "error")
        return redirect(url_for('presenta.home'))

    try:
        filename = f"{report_id}.html"
        file_path = os.path.join(records_folder, filename)
        
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"File {filename} deleted from storage.")
        else:
            print(f"Note: File {filename} was already missing from storage.")

        delete_query = text("DELETE FROM presentations WHERE id = :id")
        db.session.execute(delete_query, {"id": report_id})
        db.session.commit()

        flash(f"Report {report_id} has been permanently deleted.", "success")

    except Exception as e:
        db.session.rollback() 
        print(f"Delete Error: {e}")
        flash(f"An error occurred while deleting: {str(e)}", "error")

    return redirect(url_for('presenta.home'))


# settings route
@bp.route('/settings', methods=['GET', 'POST'])
@admin_only
def settings():
    if request.method == 'POST':
        model = request.form.get('model')
        api = request.form.get('api')
        try:
            with open(env_file, 'w') as f:
                data = {'OPENROUTER_API_KEY': api, 'MODEL': model}
                f.write(str(data))
            flash("Settings updated successfully.", 'success')
        except Exception as e:
            flash(f"Failed to save settings: {e}", 'error')
        return redirect(url_for('presenta.settings'))
    return render_template('presenta-settings.html')



@bp.route('/start-generation', methods=['POST'])
@teacher_only
def start_generation():
    print(" Generation Request Received ")
    name = request.form.get('name')
    theme_key = request.form.get('theme')
    subtopics = request.form.get('subtopics', '')
    pdf_file = request.files.get('pdf')

    report_id = generate_unique_id('PR')
    
    path = None
    if pdf_file and pdf_file.filename != '':
        filename = secure_filename(f"{report_id}_{pdf_file.filename}")
        path = os.path.join(uploads_folder, filename)
        print(f"Saving uploaded PDF to: {path}")
        pdf_file.save(path)
        print("PDF file saved successfully.")
    
    try:
        insert_sql = text("""
            INSERT INTO presentations (id, name, teacher, theme, subtopics, status)
            VALUES (:id, :name, :teacher, :theme, :subtopics, 'processing')
        """)
        db.session.execute(insert_sql, {
            'id': report_id, 'name': name, 'teacher': session['id'],
            'theme': theme_key, 'subtopics': subtopics
        })
        db.session.commit() 

    except Exception as e:
        return jsonify(success=False, message="Database error")
    

    with open(env_file, 'r') as f:
        raw_data = f.read()

    if not raw_data.strip():
        try:
            db.session.execute(
                text("DELETE FROM presentations WHERE id = :id"), 
                {'id': report_id}
            )
            db.session.commit()
        except Exception as cleanup_e:
            db.session.rollback()
            print(f"Failed to cleanup empty settings record: {cleanup_e}")

        return jsonify({
            "success": False, 
            "message": "API key not found. Please configure settings first.",
            "redirect": url_for('presenta.home') 
        }), 400

    try:
        config = eval(raw_data)
    except Exception as e:
        try:
            db.session.execute(
                text("DELETE FROM presentations WHERE id = :id"), 
                {'id': report_id}
            )
            db.session.commit()
        except Exception as cleanup_e:
            db.session.rollback()
            print(f"Failed to cleanup corrupted settings record: {cleanup_e}")

        return jsonify(success=False, message="Settings file is corrupted. Please re-save settings."), 500

    app_instance = current_app._get_current_object()
    thread = threading.Thread(
        target=presentation_worker,
        args=(app_instance, report_id, config, name, path, theme_key, subtopics, session['id'])
    )
    thread.daemon = True
    thread.start()
    print(" Generation thread started ")
    return jsonify(
    success=True, 
    quiz_id=report_id, 
    message=f"Generation for '{name}' started!"
)


# main worker function
from openai import OpenAI

def presentation_worker(app, report_id, env_data, topic_name, pdf_path, theme_key, subtopics, teacher_id):
    with app.app_context():
        try:
            socketio.emit('quiz_progress', { 'percentage': 10, 'message': 'Processing sources...', 'status': 'processing' })
            print(" Presentation worker initialized " )

            if pdf_path and os.path.exists(pdf_path):
                content_text = text_extractor(pdf_path)

            else:
                content_text = subtopics or "No additional content provided."

            theme_config = THEME_CONFIG.get(theme_key, THEME_CONFIG["minimal_light"])
            ref_file = theme_config["file"] 
            ref_path = os.path.join(plugin_dir, 'static', 'samples', 'htmls', ref_file)
            
            if not os.path.exists(ref_path):
                raise FileNotFoundError(f"Reference file not found: {ref_path}")

            with open(ref_path, "r", encoding="utf-8") as f:
                reference_html = f.read()

            print(" Reference HTML loaded ")
            socketio.emit('quiz_progress', { 'percentage': 40, 'message': 'AI is generating beautiful slides...', 'status': 'processing'})

            print(" requesting API ")

            api_key = env_data.get('OPENROUTER_API_KEY')
            model = env_data.get('MODEL', 'qwen/qwen3-coder:free')


            prompt = f"""You are an expert educational content creator.

            Generate a complete, self-contained, single-file HTML presentation exactly in the style of the reference HTML provided.

            REFERENCE HTML:
            {reference_html}

            TOPIC: {topic_name}
            ADDITIONAL CONTENT / OUTLINE:
            {content_text}

            REQUIREMENTS:
            - Keep exact same CSS, animations, navigation, MathJax, progress bar.
            - Use the accent and teal colors from the chosen theme.
            - Total slides: 15–18, well-structured for the subject (formulas for math, layers/tables for networks, quotes/analysis for literature, processes for biology, etc.).
            - Make it engaging and student-friendly.
            - Output ONLY the full valid HTML code. No explanations.
            - Add a copyright 'Made with PRESENTA - JAZARI' in the footer of the each slide on bottom left corner."""

                        # === IMPROVED OPENAI CLIENT CALL WITH BETTER DEBUGGING ===
            try:
                print(f"Attempting to connect to OpenRouter using model: {model}")

                client = OpenAI(
                    base_url="https://openrouter.ai/api/v1",
                    api_key=api_key,
                )

                print("OpenAI client created successfully")

                response = client.chat.completions.create(
                    model=model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.7,
                    max_tokens=12000,           
                )

                print(f"OpenRouter call successful. Status: {response}")

                generated_html = response.choices[0].message.content

            except Exception as e:
                print(f"OpenAI Client Error Type: {type(e).__name__}")
                print(f"Error Message: {str(e)}")
                raise Exception(f"Failed to connect to OpenRouter: {str(e)}")
            

            if "```html" in generated_html:
                generated_html = generated_html.split("```html")[1].split("```")[0]
            elif "```" in generated_html:
                generated_html = generated_html.split("```")[1].split("```")[0]


            output_path = os.path.join(records_folder, f"{report_id}.html")
            with open(output_path, "w", encoding="utf-8") as f:
                f.write(generated_html.strip())


            update_query = text("""
                UPDATE presentations 
                SET status = 'active', theme = :theme, subtopics = :subtopics
                WHERE id = :id
            """)

            try:
                db.session.execute(update_query, {
                    "id": report_id,
                    "theme": theme_key, 
                    "subtopics": subtopics 
                })
                db.session.commit()
            except Exception as e:
                db.session.rollback()
                
                try:
                    db.session.execute(
                        text("UPDATE presentations SET status = 'failed' WHERE id = :id"),
                        {'id': report_id}
                    )
                    db.session.commit()
                except Exception as db_err:
                    print(f"Failed to update error status in DB: {db_err}")

                print(f"Generation Error: {e}")

                socketio.emit('quiz_progress', {
                    'percentage': 0,
                    'message': f'Generation Failed: {str(e)}',
                    'status': 'failed'
                })

        except Exception as e:
            db.session.rollback()
            print(f"Generation Error: {e}")
            socketio.emit('quiz_progress', {
                'percentage': 0,
                'message': f'Error: {str(e)}',
                'status': 'failed'
            })