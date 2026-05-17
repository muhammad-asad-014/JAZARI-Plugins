from flask import Blueprint, render_template, abort, session, request, redirect, flash, url_for, send_from_directory, jsonify
from functools import wraps
from flask_login import current_user
import os
from core import db
import uuid
from datetime import date
from werkzeug.utils import secure_filename
import json
from flask_socketio import SocketIO, emit
import time
import threading
from core import socketio
from sqlalchemy import text
from langchain_community.document_loaders import PyMuPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_community.vectorstores import FAISS
from openai import OpenAI

plugin_dir = os.path.dirname(os.path.abspath(__file__))

embedder_folder = os.path.join(plugin_dir, '.embedder/')

embeddings_folder = os.path.join(plugin_dir, '.document_embeddings')
if not os.path.exists(embeddings_folder):
    os.mkdir(embeddings_folder)

uploads_folder = os.path.join(plugin_dir, '.uploads')
if not os.path.exists(uploads_folder):
    os.mkdir(uploads_folder)

env_file = os.path.join(plugin_dir, '.env')


bp = Blueprint('roberta', __name__, template_folder='templates', root_path=plugin_dir, static_folder='static')


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
    try:
        with open(env_file, 'r') as f:
            settings = eval(f.read())
    except:
        settings = {}
    
    return render_template('roberta-home.html', camera_id=settings.get('CAMERA_ID'))




def get_index_name(doc_path):
    return os.path.splitext(os.path.basename(doc_path))[0]


def doc_loader(file_path):
    loader = PyMuPDFLoader(file_path)
    docs = loader.load()
    return docs

def doc_chunker(docs):
    chunker = RecursiveCharacterTextSplitter(chunk_size=700, chunk_overlap=100)
    chunks = chunker.split_documents(docs)
    return chunks

def doc_processor(doc_path:str):
    try:
        if os.path.exists(embeddings_folder):
            docs = doc_loader(doc_path)
            chunks = doc_chunker(docs)
            embedder = HuggingFaceEmbeddings(model_name=embedder_folder)
            vectorDB = FAISS.from_documents(chunks, embedder)
            index_name = get_index_name(doc_path)
            vectorDB.save_local(folder_path=embeddings_folder, index_name=index_name)
            print(f"Successfully processed and saved index: {index_name}")
            return True
        else:
            print('Embeddings folder missing')
            return False
    except Exception as e:
        print(f"Error in doc_processor: {e}")
        return False
    

def doc_retriever(doc_path:str, query:str):
    try:
        embedder = HuggingFaceEmbeddings(model_name=embedder_folder)
        index_name = get_index_name(doc_path)
        index_file = os.path.join(embeddings_folder, f"{index_name}.faiss")
        
        if os.path.exists(index_file):
            vectorDB = FAISS.load_local(
                folder_path=embeddings_folder, 
                index_name=index_name, 
                embeddings=embedder, 
                allow_dangerous_deserialization=True
            )
            docs = vectorDB.similarity_search(query, k=4)
            return "\n".join([d.page_content for d in docs])
        return None
    except Exception as e:
        print(f"Error in doc_retriever: {e}")
        return None






@bp.route('/settings')
@admin_only
def settings():
    
    return render_template('roberta-settings.html')


@bp.route('/settings', methods=['POST', 'GET'])
@admin_only
def update_settings():
    # for the Admin only
    if request.method=='POST':
        model = request.form.get('model')
        api = request.form.get('api')
        camera_id = request.form.get('camera_id')
        try:
            with open(env_file, 'w') as f:
                data = {'OPENROUTER_API_KEY': api, 'MODEL': model, 'CAMERA_ID': camera_id}
                f.write(str(data))
            flash("Settings updated successfully.", 'success')
        except Exception as e:
            flash(f"Failed to save settings: {e}", 'error')
        return redirect(url_for('roberta.update_settings'))
    return render_template('roberta-settings.html')



def generate_unique_id(prefix):
    import uuid
    return f"{prefix}_{str(uuid.uuid4()).split('-')[0].upper()}"




@bp.route('/process-lecture', methods=['POST'])
def process_lecture():
    print("Files in request:", request.files)
    file = request.files.get('pdf')
    if not file:
        return jsonify(status="error", message="No file part with key 'pdf'"), 400
    
    if file.filename == '':
        return jsonify(status="error", message="No selected file"), 400
    path = None
    if file:
        filename = secure_filename(file.filename)
        path = os.path.join(uploads_folder, filename)
        file.save(path)
        # will be used later for asking questions
        session['current_pdf_path'] = path

        thread = threading.Thread(target=doc_processor, args=(path,))
        thread.start()

        return jsonify(status="success", message="Viewer opening, AI indexing started...")
    
    return jsonify(status="error"), 400



@bp.route('/ask-roberta', methods=['POST'])
@teacher_only
def ask_llm():
    data = request.get_json()
    query = data.get('query')
    doc_path = session.get('current_pdf_path')

    if not doc_path:
        return jsonify(answer="No lecture file found. Please upload a PDF first.")

    context = doc_retriever(doc_path, query)
    
    if context is None:
        return jsonify(answer="I'm still indexing the lecture. Give me a few more seconds!")
    try:
        with open(env_file, 'r') as f:
            content = f.read().replace("'", '"') 
            settings = json.loads(content) 
    except Exception as e:
        print(f"Settings error: {e}")
        return jsonify(answer="API settings not configured correctly.")

    client = OpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.get("OPENROUTER_API_KEY"),
    )

    try:
        print(f"Calling API with model: {settings.get('MODEL')}")
        completion = client.chat.completions.create(
            model=settings.get("MODEL"),
            messages=[
                {
                    "role": "system",
                    "content": (
            "You are ROBERTA (Realtime Observation of Behavior, Engagement & Reporting teaching Analytics), a helpful teaching assistant. "
            "Use the provided context to answer the question. "
            "STRICT RULES: "
            "1. Answer in 2 to 3 lines maximum. "
            "2. Do NOT use markdown (no **, no #, no lists). "
            "3. Use plain text only. "
            "4. If the answer isn't in the context, say you don't know based on the slides."
        )
                },
                {"role": "user", "content": f"Context: {context}\n\nQuestion: {query}"}
            ],
            extra_body={"reasoning": {"enabled": True}} 
        )
        answer = completion.choices[0].message.content
        
    except Exception as e:
        answer = f"roberta encountered an API error: {str(e)}"

    return jsonify(answer=answer)


import shutil

@bp.route('/cleanup-session', methods=['POST'])
@teacher_only
def cleanup_session():
    doc_path = session.get('current_pdf_path')
    
    if doc_path:
        try:
            if os.path.exists(doc_path):
                os.remove(doc_path)
            
            index_name = os.path.splitext(os.path.basename(doc_path))[0]
            for ext in ['.faiss', '.pkl']:
                file_to_del = os.path.join(embeddings_folder, f"{index_name}{ext}")
                if os.path.exists(file_to_del):
                    os.remove(file_to_del)
            
            session.pop('current_pdf_path', None)
            return jsonify(status="success", message="Session cleared")
        except Exception as e:
            return jsonify(status="error", message=str(e)), 500
            
    return jsonify(status="success"), 200