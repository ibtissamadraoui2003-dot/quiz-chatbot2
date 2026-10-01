"""
app.py
-------
Point d'entrée unique de l'application : sert le frontend (HTML/CSS/JS)
ET expose l'API utilisée par le chat. Un seul serveur, un seul port
(contrairement à l'ancienne version du projet qui avait deux serveurs
séparés sur deux ports différents).

Lancement :  python app.py   (depuis le dossier backend/)
"""

import os
import sys
import traceback

from flask import Flask, request, jsonify, send_from_directory
from werkzeug.utils import secure_filename

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from database import Database
from ai_provider import AIProvider
from documents_service import DocumentsService
from quiz_service import QuizService
from chat_service import ChatService

BACKEND_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(BACKEND_DIR)
FRONTEND_DIR = os.path.join(BASE_DIR, 'frontend')

ALLOWED_EXTENSIONS = {'pdf'}

app = Flask(__name__, static_folder=None)
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16 Mo max par PDF

UPLOAD_FOLDER = os.getenv('UPLOAD_FOLDER') or os.path.join(BASE_DIR, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

db = Database(os.path.join(BACKEND_DIR, 'quiz.db'))
ai = AIProvider()
documents_service = DocumentsService(db, ai, UPLOAD_FOLDER)
quiz_service = QuizService(db, ai)
chat_service = ChatService(db, ai, quiz_service)


# ----------------------------------------------------------------------
# CORS (utile si un jour le frontend tourne sur un autre port/domaine que
# l'API ; sans effet quand tout est servi par ce même serveur Flask).
# Flask gère déjà automatiquement les requêtes OPTIONS de préflight pour
# chaque route, donc il n'y a rien d'autre à faire que d'ajouter les
# en-têtes à chaque réponse.
# ----------------------------------------------------------------------
@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    return response


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def enrich_quiz_message(msg):
    """Ajoute à un message de type 'quiz' l'état déjà répondu (ou non) de
    chaque question, en relisant les tentatives enregistrées en base.
    C'est ce qui permet à un quiz de garder son état si on recharge la page."""
    if not msg or msg.get('message_type') != 'quiz' or not msg.get('metadata'):
        return msg
    questions = msg['metadata'].get('questions', [])
    ids = [q['id'] for q in questions if 'id' in q]
    attempts = db.get_latest_attempts(ids) if ids else {}
    for q in questions:
        attempt = attempts.get(q.get('id'))
        if attempt:
            q['answered'] = True
            q['selected_index'] = attempt['selected_index']
            q['is_correct'] = bool(attempt['is_correct'])
        else:
            q.setdefault('answered', False)
    return msg


# ----------------------------------------------------------------------
# Frontend
# ----------------------------------------------------------------------
@app.route('/')
def index():
    return send_from_directory(FRONTEND_DIR, 'index.html')


@app.route('/style.css')
def style_css():
    return send_from_directory(FRONTEND_DIR, 'style.css')


@app.route('/script.js')
def script_js():
    return send_from_directory(FRONTEND_DIR, 'script.js')


@app.route('/favicon.ico')
def favicon():
    return '', 204


# ----------------------------------------------------------------------
# Santé / diagnostic
# ----------------------------------------------------------------------
@app.route('/api/health', methods=['GET'])
def health():
    data = {
        'status': 'ok',
        'ia': ai.status(),
        'documents': len(db.get_all_documents()),
    }
    # ?check=1 : fait un tout petit appel pour vérifier que la clé ET le modèle
    # fonctionnent vraiment (résultat mis en cache : un seul appel par démarrage).
    if request.args.get('check'):
        data['ia_test'] = ai.self_test()
        data['ia'] = ai.status()
    return jsonify(data)


# ----------------------------------------------------------------------
# Documents (cours + examens passés)
# ----------------------------------------------------------------------
@app.route('/api/documents/upload', methods=['POST'])
def upload_document():
    if 'pdf' not in request.files:
        return jsonify({'error': 'Aucun fichier PDF fourni.'}), 400

    pdf_file = request.files['pdf']
    if pdf_file.filename == '':
        return jsonify({'error': 'Aucun fichier sélectionné.'}), 400
    if not allowed_file(pdf_file.filename):
        return jsonify({'error': 'Seuls les fichiers PDF sont autorisés.'}), 400

    doc_type = request.form.get('doc_type', 'cours')
    if doc_type not in ('cours', 'examen'):
        doc_type = 'cours'

    filename = secure_filename(pdf_file.filename) or 'document.pdf'
    title = (request.form.get('title') or os.path.splitext(pdf_file.filename)[0]).strip()
    subject = (request.form.get('subject') or 'Non spécifié').strip()

    try:
        result = documents_service.process_upload(pdf_file, filename, title, subject, doc_type)
        if result.get('error'):
            return jsonify({'error': result['error']}), 422

        chat_msg = result['chat_message']
        saved_message = db.add_message(
            'assistant', chat_msg['content'], chat_msg['message_type'], chat_msg['metadata']
        )

        return jsonify({
            'success': True,
            'document': result['document'],
            'message': saved_message,
        })
    except Exception as e:
        print(f"❌ Erreur upload document : {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/documents', methods=['GET'])
def list_documents():
    doc_type = request.args.get('type')
    return jsonify(db.get_all_documents(doc_type))


@app.route('/api/documents/<int:document_id>', methods=['GET'])
def get_document(document_id):
    doc = db.get_document(document_id)
    if not doc:
        return jsonify({'error': 'Document introuvable.'}), 404
    return jsonify(doc)


@app.route('/api/documents/<int:document_id>', methods=['DELETE'])
def delete_document(document_id):
    filepath = db.delete_document(document_id)
    if filepath is None:
        return jsonify({'error': 'Document introuvable.'}), 404
    if filepath and os.path.exists(filepath):
        try:
            os.remove(filepath)
        except OSError as e:
            print(f"⚠️ Impossible de supprimer le fichier physique : {e}")
    return jsonify({'success': True})


@app.route('/api/documents/<int:document_id>/quiz', methods=['POST'])
def generate_quiz_for_document(document_id):
    doc = db.get_document(document_id)
    if not doc:
        return jsonify({'error': 'Document introuvable.'}), 404

    data = request.get_json(silent=True) or {}
    try:
        num_questions = max(3, min(int(data.get('num_questions', 8)), 20))
    except (TypeError, ValueError):
        num_questions = 8
    difficulty = data.get('difficulty', 'moyen')
    if difficulty not in ('facile', 'moyen', 'difficile'):
        difficulty = 'moyen'

    try:
        result = quiz_service.generate_quiz([document_id], num_questions, difficulty)
        if result.get('error'):
            return jsonify({'error': result['error']}), 422

        intro = quiz_service.build_intro(result, difficulty)
        message = db.add_message('assistant', intro, 'quiz', {
            'document_ids': [document_id],
            'questions': result['questions'],
        })
        return jsonify({'success': True, 'message': enrich_quiz_message(message)})
    except Exception as e:
        print(f"❌ Erreur génération quiz : {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ----------------------------------------------------------------------
# Chat (mémoire de la conversation)
# ----------------------------------------------------------------------
@app.route('/api/chat/history', methods=['GET'])
def chat_history():
    messages = db.get_all_messages()
    return jsonify([enrich_quiz_message(m) for m in messages])


@app.route('/api/chat/message', methods=['POST'])
def chat_message():
    data = request.get_json(silent=True) or {}
    content = data.get('content', '')
    try:
        result = chat_service.handle_message(content)
        if result.get('error'):
            return jsonify({'error': result['error']}), 400
        result['assistant_messages'] = [enrich_quiz_message(m) for m in result['assistant_messages']]
        return jsonify(result)
    except Exception as e:
        print(f"❌ Erreur chat : {e}")
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/chat/reset', methods=['DELETE'])
def reset_chat():
    db.clear_messages()
    return jsonify({'success': True})


# ----------------------------------------------------------------------
# Quiz : réponse à une question
# ----------------------------------------------------------------------
@app.route('/api/quiz/answer', methods=['POST'])
def answer_quiz_question():
    data = request.get_json(silent=True) or {}
    question_id = data.get('question_id')
    selected_index = data.get('selected_index')
    if question_id is None or selected_index is None:
        return jsonify({'error': 'question_id et selected_index sont requis.'}), 400

    try:
        result = quiz_service.record_answer(int(question_id), int(selected_index))
        if result.get('error'):
            return jsonify({'error': result['error']}), 404
        return jsonify(result)
    except (TypeError, ValueError):
        return jsonify({'error': 'question_id et selected_index doivent être des nombres.'}), 400


# ----------------------------------------------------------------------
# Progression (points forts / points faibles)
# ----------------------------------------------------------------------
@app.route('/api/progress', methods=['GET'])
def progress():
    return jsonify(db.get_concept_stats())


# ----------------------------------------------------------------------
if __name__ == '__main__':
    port = int(os.getenv('PORT', 5001))
    print("=" * 55)
    print("🎓  Assistant de révision IA")
    print(f"🤖  IA : {ai.status()}")
    print(f"📁  Uploads : {UPLOAD_FOLDER}")
    print(f"🌐  http://localhost:{port}")
    print("=" * 55)
    app.run(debug=True, port=port, host='0.0.0.0')
