"""
database.py
------------
Toute la couche base de données (SQLite) de l'application.
Un seul fichier quiz.db stocke : les documents (cours + examens passés),
leurs extraits de texte (chunks), les questions de quiz, les réponses
données par l'élève et l'historique complet de la conversation.

Ce module ne fait AUCUN appel IA et ne connaît rien du traitement PDF :
il ne fait que lire/écrire des données. C'est ce qui permet au chatbot
de « tout mémoriser » : rien ne vit seulement en mémoire du navigateur,
tout est relu depuis ce fichier à chaque rechargement de la page.
"""

import sqlite3
import json
import os
from datetime import datetime


class Database:
    def __init__(self, db_path='quiz.db'):
        self.db_path = db_path
        print(f"📁 Base de données : {os.path.abspath(self.db_path)}")
        self.init_db()

    def get_connection(self):
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute('PRAGMA foreign_keys = ON')
        return conn

    def init_db(self):
        conn = self.get_connection()
        cur = conn.cursor()

        # Documents : cours ET examens passés (doc_type distingue les deux)
        cur.execute('''
            CREATE TABLE IF NOT EXISTS documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL,
                title TEXT,
                subject TEXT DEFAULT 'Non spécifié',
                doc_type TEXT NOT NULL DEFAULT 'cours',   -- 'cours' ou 'examen'
                page_count INTEGER DEFAULT 0,
                summary_json TEXT,                        -- résumé / points clés / concepts (JSON)
                processed BOOLEAN DEFAULT 0,
                upload_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        # Extraits de texte de chaque document, utilisés pour la recherche
        # de contexte (mémoire du contenu) et la génération de quiz.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS chunks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER NOT NULL,
                chunk_index INTEGER NOT NULL,
                content TEXT NOT NULL,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE CASCADE
            )
        ''')

        # Questions de quiz : générées par l'IA à partir d'un cours,
        # ou extraites d'un examen passé.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id INTEGER,
                question TEXT NOT NULL,
                options TEXT NOT NULL,          -- JSON: liste de 4 options
                correct_index INTEGER NOT NULL,
                explanation TEXT,
                concept TEXT DEFAULT 'Général',  -- sert au suivi des points faibles
                difficulty TEXT DEFAULT 'moyen',
                source TEXT DEFAULT 'genere',    -- 'genere' ou 'examen'
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (document_id) REFERENCES documents (id) ON DELETE SET NULL
            )
        ''')

        # Chaque réponse donnée par l'élève à une question, pour calculer
        # ses points forts / points faibles au fil du temps.
        cur.execute('''
            CREATE TABLE IF NOT EXISTS quiz_attempts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question_id INTEGER NOT NULL,
                selected_index INTEGER NOT NULL,
                is_correct BOOLEAN NOT NULL,
                answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (question_id) REFERENCES questions (id) ON DELETE CASCADE
            )
        ''')

        # Historique complet de la conversation (la « mémoire » du chat).
        cur.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                role TEXT NOT NULL,              -- 'user' ou 'assistant'
                message_type TEXT DEFAULT 'text', -- text / summary / exam_analysis / quiz / quiz_result / weak_points
                content TEXT,
                metadata_json TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')

        conn.commit()
        conn.close()
        print("✅ Base de données prête")

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------
    def save_document(self, filename, filepath, title, subject, doc_type, page_count):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO documents (filename, filepath, title, subject, doc_type, page_count, processed)
            VALUES (?, ?, ?, ?, ?, ?, 0)
        ''', (filename, filepath, title, subject, doc_type, page_count))
        conn.commit()
        doc_id = cur.lastrowid
        conn.close()
        return doc_id

    def save_chunks(self, document_id, chunks):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.executemany(
            'INSERT INTO chunks (document_id, chunk_index, content) VALUES (?, ?, ?)',
            [(document_id, i, c) for i, c in enumerate(chunks)]
        )
        conn.commit()
        conn.close()

    def set_document_summary(self, document_id, summary_dict):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute(
            'UPDATE documents SET summary_json = ?, processed = 1 WHERE id = ?',
            (json.dumps(summary_dict, ensure_ascii=False), document_id)
        )
        conn.commit()
        conn.close()

    def get_all_documents(self, doc_type=None):
        conn = self.get_connection()
        cur = conn.cursor()
        if doc_type:
            cur.execute('SELECT * FROM documents WHERE doc_type = ? ORDER BY upload_date DESC', (doc_type,))
        else:
            cur.execute('SELECT * FROM documents ORDER BY upload_date DESC')
        rows = cur.fetchall()
        conn.close()
        return [self._doc_to_dict(r) for r in rows]

    def get_document(self, document_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM documents WHERE id = ?', (document_id,))
        row = cur.fetchone()
        conn.close()
        return self._doc_to_dict(row) if row else None

    def get_chunks(self, document_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM chunks WHERE document_id = ? ORDER BY chunk_index', (document_id,))
        rows = cur.fetchall()
        conn.close()
        return [{'document_id': document_id, 'content': r['content']} for r in rows]

    def get_chunks_for_documents(self, document_ids):
        if not document_ids:
            return []
        conn = self.get_connection()
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in document_ids)
        cur.execute(f'''
            SELECT c.*, d.title as doc_title FROM chunks c
            JOIN documents d ON d.id = c.document_id
            WHERE c.document_id IN ({placeholders})
            ORDER BY c.document_id, c.chunk_index
        ''', document_ids)
        rows = cur.fetchall()
        conn.close()
        return [{'document_id': r['document_id'], 'content': r['content'], 'doc_title': r['doc_title']} for r in rows]

    def delete_document(self, document_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT filepath FROM documents WHERE id = ?', (document_id,))
        row = cur.fetchone()
        if not row:
            conn.close()
            return None
        filepath = row['filepath']
        # Grâce à ON DELETE CASCADE / SET NULL, chunks et questions sont gérés proprement
        cur.execute('DELETE FROM documents WHERE id = ?', (document_id,))
        conn.commit()
        conn.close()
        return filepath

    def _doc_to_dict(self, row):
        d = dict(row)
        if d.get('summary_json'):
            try:
                d['summary'] = json.loads(d['summary_json'])
            except (json.JSONDecodeError, TypeError):
                d['summary'] = None
        else:
            d['summary'] = None
        d.pop('summary_json', None)
        return d

    # ------------------------------------------------------------------
    # Questions / Quiz
    # ------------------------------------------------------------------
    def add_question(self, document_id, question, options, correct_index,
                      explanation='', concept='Général', difficulty='moyen', source='genere'):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO questions (document_id, question, options, correct_index, explanation, concept, difficulty, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (document_id, question, json.dumps(options, ensure_ascii=False), correct_index,
              explanation, concept, difficulty, source))
        conn.commit()
        qid = cur.lastrowid
        conn.close()
        return qid

    def get_question(self, question_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM questions WHERE id = ?', (question_id,))
        row = cur.fetchone()
        conn.close()
        return self._question_to_dict(row) if row else None

    def get_questions(self, question_ids):
        if not question_ids:
            return []
        conn = self.get_connection()
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in question_ids)
        cur.execute(f'SELECT * FROM questions WHERE id IN ({placeholders})', question_ids)
        rows = cur.fetchall()
        conn.close()
        by_id = {r['id']: self._question_to_dict(r) for r in rows}
        # préserve l'ordre demandé
        return [by_id[qid] for qid in question_ids if qid in by_id]

    def _question_to_dict(self, row):
        d = dict(row)
        d['options'] = json.loads(d['options'])
        return d

    def record_attempt(self, question_id, selected_index, is_correct):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO quiz_attempts (question_id, selected_index, is_correct)
            VALUES (?, ?, ?)
        ''', (question_id, selected_index, is_correct))
        conn.commit()
        conn.close()

    def get_latest_attempts(self, question_ids):
        """Retourne, pour chaque question_id fourni, sa dernière tentative (ou None)."""
        if not question_ids:
            return {}
        conn = self.get_connection()
        cur = conn.cursor()
        placeholders = ','.join('?' for _ in question_ids)
        cur.execute(f'''
            SELECT qa.* FROM quiz_attempts qa
            INNER JOIN (
                SELECT question_id, MAX(id) as max_id FROM quiz_attempts
                WHERE question_id IN ({placeholders})
                GROUP BY question_id
            ) latest ON latest.max_id = qa.id
        ''', question_ids)
        rows = cur.fetchall()
        conn.close()
        return {r['question_id']: dict(r) for r in rows}

    def get_concept_stats(self, subject=None):
        """Agrège les tentatives par concept pour repérer points forts / points faibles."""
        conn = self.get_connection()
        cur = conn.cursor()
        query = '''
            SELECT q.concept as concept,
                   COUNT(a.id) as total,
                   SUM(CASE WHEN a.is_correct THEN 1 ELSE 0 END) as correct
            FROM quiz_attempts a
            JOIN questions q ON q.id = a.question_id
        '''
        params = []
        if subject:
            query += '''
                LEFT JOIN documents d ON d.id = q.document_id
                WHERE (d.subject = ? OR q.document_id IS NULL)
            '''
            params.append(subject)
        query += ' GROUP BY q.concept ORDER BY q.concept'
        cur.execute(query, params)
        rows = cur.fetchall()
        conn.close()

        stats = []
        for r in rows:
            total = r['total']
            correct = r['correct'] or 0
            stats.append({
                'concept': r['concept'],
                'total': total,
                'correct': correct,
                'wrong': total - correct,
                'accuracy': round(100 * correct / total) if total else 0
            })
        return stats

    # ------------------------------------------------------------------
    # Messages (mémoire de la conversation)
    # ------------------------------------------------------------------
    def add_message(self, role, content, message_type='text', metadata=None):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('''
            INSERT INTO messages (role, message_type, content, metadata_json)
            VALUES (?, ?, ?, ?)
        ''', (role, message_type, content, json.dumps(metadata, ensure_ascii=False) if metadata is not None else None))
        conn.commit()
        msg_id = cur.lastrowid
        conn.close()
        return self.get_message(msg_id)

    def get_message(self, message_id):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM messages WHERE id = ?', (message_id,))
        row = cur.fetchone()
        conn.close()
        return self._message_to_dict(row) if row else None

    def get_all_messages(self):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM messages ORDER BY id ASC')
        rows = cur.fetchall()
        conn.close()
        return [self._message_to_dict(r) for r in rows]

    def get_recent_messages(self, limit=8):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('SELECT * FROM messages ORDER BY id DESC LIMIT ?', (limit,))
        rows = cur.fetchall()
        conn.close()
        return [self._message_to_dict(r) for r in reversed(rows)]

    def clear_messages(self):
        conn = self.get_connection()
        cur = conn.cursor()
        cur.execute('DELETE FROM messages')
        conn.commit()
        conn.close()

    def _message_to_dict(self, row):
        d = dict(row)
        d['metadata'] = json.loads(d['metadata_json']) if d.get('metadata_json') else None
        d.pop('metadata_json', None)
        return d
