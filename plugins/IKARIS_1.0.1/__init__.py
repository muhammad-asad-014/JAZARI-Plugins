from flask import Blueprint, render_template, abort, session, request, redirect, flash, url_for, send_from_directory, jsonify
from functools import wraps
from flask_login import current_user
import os
from core import db
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.graphics.shapes import Drawing
from reportlab.lib import colors
from reportlab.graphics.charts.barcharts import VerticalBarChart
import uuid
from reportlab.lib.units import inch
from datetime import date
from werkzeug.utils import secure_filename
import json
from flask_socketio import SocketIO, emit
import time
import threading
from core import socketio
from sqlalchemy import text

import markdown
from weasyprint import HTML, CSS


plugin_dir = os.path.dirname(os.path.abspath(__file__))
records_folder = os.path.join(plugin_dir, 'records')
if not os.path.exists(records_folder):
    os.mkdir(records_folder)

uploads_folder = os.path.join(plugin_dir, '.uploads')
if not os.path.exists(uploads_folder):
    os.mkdir(uploads_folder)

env_file = os.path.join(plugin_dir, '.env')


bp = Blueprint('ikaris', __name__, template_folder='templates', root_path=plugin_dir, static_folder='static')


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

@bp.route('/home/')
@teacher_only
def home():
    # for teachers
    from core.models import Notes
    active_notes = Notes.query.filter(
        Notes.teacher == session['id'],
        Notes.status == 'processing'
    ).order_by(Notes.date_created.desc()).all()

    prev_rec = Notes.query.filter(
        Notes.teacher == session['id'],
        Notes.status == 'active'
    ).order_by(Notes.date_created.desc()).all()
    return render_template('ikaris-home.html', data=prev_rec, record_folder = records_folder, active_data=active_notes)

@bp.route('/download/<report_id>/')
@teacher_only
def download_report(report_id):
    filename = f"{report_id}.pdf"
    return send_from_directory(records_folder, filename, as_attachment=True)


@bp.route('/delete/<report_id>/')
@teacher_only
def delete_report(report_id):
    from core.models import Notes
    report = Notes.query.filter_by(id=report_id).first()

    if not report:
        flash("Error: Report no longer exists.", "error")
        return redirect(url_for('ikaris.home'))

    try:
        filename = f"{report_id}.pdf"
        file_path = os.path.join(records_folder, filename)
        
        if os.path.exists(file_path):
            os.remove(file_path)
            print(f"File {filename} deleted from storage.")
        else:
            print(f"Note: File {filename} was already missing from storage.")

        db.session.delete(report)
        db.session.commit()

        flash(f"Report {report_id} has been permanently deleted.", "success")

    except Exception as e:
        db.session.rollback() 
        print(f"Delete Error: {e}")
        flash(f"An error occurred while deleting: {str(e)}", "error")

    return redirect(url_for('ikaris.home'))




@bp.route('/settings')
@admin_only
def settings():
    
    return render_template('ikaris-settings.html')


@bp.route('/settings', methods=['POST', 'GET'])
@admin_only
def update_settings():
    # for the Admin only
    if request.method=='POST':
        model = request.form.get('model')
        api = request.form.get('api')
        try:
            with open(env_file, 'w') as f:
                data = {'OPENROUTER_API_KEY': api, 'MODEL': model}
                f.write(str(data))
            flash("Settings updated successfully.", 'success')
        except Exception as e:
            flash(f"Failed to save settings: {e}", 'error')
        return redirect(url_for('ikaris.update_settings'))
    return render_template('ikaris-settings.html')




def export_to_pdf(md_content, report_id):
    
    html_content = markdown.markdown(md_content, extensions=['extra', 'nl2br'])

    css_style = f"""
    @page {{
        size: A4;
        margin: 2.5cm;
        @bottom-right {{
            content: "Page " counter(page) " of " counter(pages);
            font-size: 9pt;
            color: #666;
        }}
    }}
    body {{
        font-family: 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
        line-height: 1.6;
        color: #333;
        font-size: 11pt;
    }}
    h1 {{
        color: #4f46e5; /* Indigo accent */
        border-bottom: 2px solid #4f46e5;
        padding-bottom: 10px;
        font-size: 24pt;
        margin-top: 0;
    }}
    h2 {{
        color: #1e1b4b;
        margin-top: 1.5em;
        border-left: 5px solid #4f46e5;
        padding-left: 10px;
    }}
    h3 {{ color: #4338ca; }}
    blockquote {{
        font-style: italic;
        background: #f9fafb;
        border-left: 4px solid #d1d5db;
        padding: 10px 20px;
        margin: 20px 0;
    }}
    table {{
        width: 100%;
        border-collapse: collapse;
        margin: 20px 0;
    }}
    th, td {{
        border: 1px solid #e5e7eb;
        padding: 12px;
        text-align: left;
    }}
    th {{ background-color: #f3f4f6; font-weight: bold; }}
    .footer {{
        margin-top: 50px;
        font-size: 8pt;
        text-align: center;
        color: #9ca3af;
        border-top: 1px solid #e5e7eb;
        padding-top: 10px;
    }}
    """

    full_html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="utf-8">
        <title>{report_id}</title>
    </head>
    <body>
        <div class="content">
            {html_content}
        </div>
        <div class="footer">
            Generated by JAZARI • IKARIS
        </div>
    </body>
    </html>
    """

    output_path = os.path.join(records_folder, f"{report_id}.pdf")

    HTML(string=full_html).write_pdf(
        output_path, 
        stylesheets=[CSS(string=css_style)]
    )
    print(f"Successfully generated: {output_path}")


def generate_notes(app, id, data, name, teacher, pdf_path, mode, status):
    with app.app_context():
        from core.models import Notes
        
        try:
            socketio.emit('quiz_progress', {'percentage': 10, 'message': 'Extracting data...', 'status': 'processing'})
            
            from utils.utilities import notes_generator
            generated_notes = notes_generator(api_key=data['OPENROUTER_API_KEY'], model=data['MODEL'], mode=mode, lecture_doc=pdf_path)
            
            socketio.emit('quiz_progress', {'percentage': 50, 'message': 'AI generating notes...', 'status': 'processing'})
            
            if not generated_notes or generated_notes.get('redflag'):
                raise Exception("AI flagged the content or failed to generate.")

            time.sleep(2) 
            socketio.emit('quiz_progress', {'percentage': 80, 'message': 'Saving to database...', 'status': 'processing'})
            
            try:
                export_to_pdf(generated_notes['notes'], id)
            except Exception as e:
                db.session.rollback()
                print(f"Generation Error: {e}")
                from core.models import Notes
                try:
                    Notes.query.filter_by(id=id).delete()
                    db.session.commit()
                    print(f"Successfully deleted stuck notes {id} from database.")
                except Exception as cleanup_e:
                    print(f"Failed to delete stuck notes: {cleanup_e}")

                socketio.emit('quiz_progress', {'percentage': 0, 'message': f'Error: {str(e)}', 'status': 'error'})
            


            notes = Notes.query.get(id)
            if not notes:
                raise Exception("Notes record lost during processing.")
                
            notes.status = 'active'
            db.session.commit()
            
            
            socketio.emit('quiz_progress', {'percentage': 100, 'message': 'Notes Ready!', 'status': 'completed'})

        except Exception as e:
            db.session.rollback()
            print(f"Generation Error: {e}")
            from core.models import Notes
            try:
                Notes.query.filter_by(id=id).delete()
                db.session.commit()
                print(f"Successfully deleted stuck notes {id} from database.")
            except Exception as cleanup_e:
                print(f"Failed to delete stuck notes: {cleanup_e}")

            socketio.emit('quiz_progress', {'percentage': 0, 'message': f'Error: {str(e)}', 'status': 'error'})

        finally:
            db.session.remove()


def generate_unique_id(prefix):
    import uuid
    return f"{prefix}_{str(uuid.uuid4()).split('-')[0].upper()}"


@bp.route('/start-generation', methods=['POST'])
def start_generation():
    name = request.form.get('name')
    pdf_file = request.files.get('pdf')
    mode = request.form.get('mode')
    if not name:
        flash("Please provide a name for the notes.", "error")
        return redirect(url_for('ikaris.home'))
    
    if not pdf_file or pdf_file.filename == '':
        flash("No PDF file selected. Please upload a document.", "error")
        return redirect(url_for('ikaris.home'))
    teacher = session['id']
    status = 'processing'
    id = generate_unique_id('NT')
    path = None
    print(id, name, teacher, pdf_file, mode, status)
    if pdf_file:
        filename = secure_filename(pdf_file.filename)
        path = os.path.join(uploads_folder, filename)
        pdf_file.save(path)
    try:
        from core.models import Notes
        pending_notes = Notes(
            id=id,
            name=name,
            teacher=teacher,
            status=status,
        )
        db.session.add(pending_notes)
        db.session.commit()
    except Exception as e:
        return jsonify(success=False, message="Database error")

    from flask import current_app 
    with open(env_file, 'r') as f:
        data = f.read()
        if data == '':
            try:
                from core.models import Notes
                Notes.query.filter_by(id=id).delete()
                db.session.commit()
            except Exception as cleanup_e:
                print(f"Failed to delete stuck quiz: {cleanup_e}")
                return jsonify({
                "success": False, 
                "message": "No API key found. Please update your settings.",
                "redirect": url_for('ikaris.home') 
            }), 400
        else:
            try:
                data = eval(data)
            except Exception as e:
                from core.models import Notes
                Notes.query.filter_by(id=id).delete()
                db.session.commit()
                
                print(f"Failed to delete stuck quiz: {e}")
                return jsonify(success=False, message="Corrupted settings file."), 500
      
    app_instance = current_app._get_current_object()
    
    thread = threading.Thread(
        target=generate_notes, 
        args=(app_instance, id, data, name, teacher, path, mode, status)
    )
    thread.daemon = True #
    thread.start()
    flash(f"Generation for '{name}' started!", "success")
    redirect(url_for('ikaris.home'))
    return jsonify(success=True, quiz_id=id)


