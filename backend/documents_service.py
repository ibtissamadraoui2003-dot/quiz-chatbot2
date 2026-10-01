"""
documents_service.py
----------------------
Orchestration de ce qui se passe quand un élève envoie un PDF :

  - si c'est un COURS  -> l'IA produit un résumé + les points essentiels
                          + la liste des notions clés (utilisées ensuite
                          pour repérer les points faibles de l'élève).
  - si c'est un EXAMEN PASSÉ -> l'IA repère les questions déjà posées,
                          les thèmes récurrents, et mémorise tout ça pour
                          s'en inspirer lors des prochains quiz.

Si aucune clé API n'est configurée, une version simplifiée (sans IA) est
produite automatiquement pour que l'application reste utilisable.
"""

import os
import re
from collections import Counter

from pdf_processor import PDFProcessor
from retrieval import STOPWORDS_FR


class DocumentsService:
    def __init__(self, db, ai, upload_folder='uploads'):
        self.db = db
        self.ai = ai
        self.pdf = PDFProcessor(upload_folder)

    # ------------------------------------------------------------------
    def process_upload(self, pdf_file, filename, title, subject, doc_type):
        """Traite un PDF uploadé de bout en bout et renvoie tout ce qu'il
        faut pour répondre à la requête HTTP et alimenter le chat."""
        filepath = self.pdf.save_upload(pdf_file, filename)
        chunks, page_count = self.pdf.process(filepath)

        if not chunks:
            os.remove(filepath) if os.path.exists(filepath) else None
            return {'error': "Impossible d'extraire du texte de ce PDF (page scannée en image, fichier protégé...)."}

        document_id = self.db.save_document(filename, filepath, title, subject, doc_type, page_count)
        self.db.save_chunks(document_id, chunks)

        sample = PDFProcessor.sample_for_overview(chunks)

        if doc_type == 'examen':
            analysis = self._analyze_exam(title, subject, sample)
            self.db.set_document_summary(document_id, analysis)
            self._store_extracted_questions(document_id, subject, analysis.get('questions_extraites', []))
            chat_message = self._build_exam_chat_message(document_id, title, subject, page_count, analysis)
        else:
            summary = self._summarize_course(title, subject, sample)
            self.db.set_document_summary(document_id, summary)
            chat_message = self._build_course_chat_message(document_id, title, subject, page_count, summary)

        document = self.db.get_document(document_id)
        return {'document': document, 'chat_message': chat_message}

    # ------------------------------------------------------------------
    # COURS : résumé + points essentiels + concepts
    # ------------------------------------------------------------------
    def _summarize_course(self, title, subject, sample_text):
        if self.ai.available():
            system = (
                "Tu es un excellent professeur qui aide des étudiants à réviser efficacement. "
                "Tu réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, sans balises markdown."
            )
            user = f'''Voici un extrait d'un cours intitulé « {title} » (matière : {subject}).

TEXTE DU COURS :
"""
{sample_text}
"""

Analyse ce cours et renvoie un JSON avec exactement cette forme :
{{
  "summary": "un résumé clair en 3 à 5 phrases, qui donne une vue d'ensemble du cours",
  "key_points": ["point essentiel 1", "point essentiel 2", "... (entre 5 et 8 points, courts et concrets)"],
  "concepts": ["notion clé 1", "notion clé 2", "... (entre 5 et 10 notions, 1 à 3 mots chacune)"]
}}

Écris en français, simple et pédagogique, comme une fiche de révision.'''

            result = self.ai.complete_json(system, user, max_tokens=1400)
            if result and result.get('summary'):
                result.setdefault('key_points', [])
                result.setdefault('concepts', [])
                return result

        # Mode dégradé (sans IA, ou si l'IA a échoué) : heuristique simple
        return self._fallback_summary(sample_text)

    def _degraded_reason(self):
        """Pourquoi on est en mode simplifié : pas de clé, ou appel IA en échec ?"""
        if not self.ai.available():
            return "Aucune clé API IA n'est configurée dans backend/.env"
        return self.ai.last_error or "L'IA n'a pas répondu"

    def _fallback_summary(self, sample_text):
        sentences = [s.strip() for s in re.split(r'(?<=[.!?])\s+', sample_text) if len(s.strip()) > 25]
        return {
            'summary': ' '.join(sentences[:3]) or "Résumé indisponible.",
            'key_points': sentences[3:9],
            'concepts': self._guess_concepts(sample_text),
            'degraded': True,
            'degraded_reason': self._degraded_reason(),
        }

    def _guess_concepts(self, text, max_concepts=8):
        words = re.findall(r"[A-ZÀÂÉÈÊËÎÏÔÖÙÛÜÇ][a-zàâäéèêëïîôöùûüç]{3,}", text)
        counts = Counter(w for w in words if w.lower() not in STOPWORDS_FR)
        return [w for w, _ in counts.most_common(max_concepts)]

    def _build_course_chat_message(self, document_id, title, subject, page_count, summary):
        degraded = summary.get('degraded')
        intro = f"J'ai lu ton cours **{title}** ({subject}, {page_count} page(s))."
        if degraded:
            reason = summary.get('degraded_reason') or "L'IA n'a pas répondu"
            intro += f"\n\n_({reason} : voici une analyse simplifiée.)_"
        else:
            intro += " Voici ce qu'il faut retenir :"
        return {
            'content': intro,
            'message_type': 'summary',
            'metadata': {
                'document_id': document_id,
                'title': title,
                'subject': subject,
                'doc_type': 'cours',
                'summary': summary.get('summary', ''),
                'key_points': summary.get('key_points', []),
                'concepts': summary.get('concepts', []),
            }
        }

    # ------------------------------------------------------------------
    # EXAMEN PASSÉ : thèmes récurrents + questions déjà tombées
    # ------------------------------------------------------------------
    def _analyze_exam(self, title, subject, sample_text):
        if self.ai.available():
            system = (
                "Tu es un assistant qui aide un étudiant à réviser à partir de ses examens passés. "
                "Tu réponds UNIQUEMENT avec un objet JSON valide, sans texte autour, sans balises markdown."
            )
            user = f'''Voici le contenu d'un examen passé intitulé « {title} » (matière : {subject}).

TEXTE DE L'EXAMEN :
"""
{sample_text}
"""

Analyse ce document et renvoie un JSON avec exactement cette forme :
{{
  "resume": "1 à 2 phrases décrivant ce que couvre cet examen",
  "themes": ["thème récurrent 1", "thème 2", "... (3 à 6 thèmes)"],
  "questions_extraites": [
    {{"question": "texte d'une question réellement présente dans le document", "reponse": "réponse si elle est visible dans le texte, sinon une chaîne vide", "concept": "thème lié à cette question"}}
  ]
}}

Extrais jusqu'à 8 questions réellement présentes dans le texte, sans en inventer.
Si le document ne contient aucune question identifiable, renvoie "questions_extraites": [].
Écris en français.'''

            result = self.ai.complete_json(system, user, max_tokens=1600)
            if result and (result.get('resume') or result.get('themes')):
                result.setdefault('themes', [])
                result.setdefault('questions_extraites', [])
                return result

        return {
            'resume': "Document ajouté à ta mémoire de révision (analyse simplifiée).",
            'themes': self._guess_concepts(sample_text),
            'questions_extraites': [],
            'degraded': True,
            'degraded_reason': self._degraded_reason(),
        }

    def _store_extracted_questions(self, document_id, subject, questions_extraites):
        """Les questions d'examen avec une réponse claire sont gardées en mémoire
        comme référence, mais ne sont PAS ajoutées telles quelles au jeu de quiz
        (elles n'ont pas 4 options). Le quiz génère toujours des questions
        fraîches, inspirées de ces thèmes, via quiz_service."""
        # Rien à stocker en table `questions` ici : ces questions restent dans
        # summary_json du document (consultables et citables par le chat),
        # ce qui suffit à la « mémorisation » demandée sans fausser le format
        # QCM utilisé par le quiz interactif.
        return

    def _build_exam_chat_message(self, document_id, title, subject, page_count, analysis):
        degraded = analysis.get('degraded')
        intro = f"J'ai analysé l'examen passé **{title}** ({subject}, {page_count} page(s)) et je le garde en mémoire."
        if degraded:
            reason = analysis.get('degraded_reason') or "L'IA n'a pas répondu"
            intro += f"\n\n_({reason} : voici une analyse simplifiée.)_"
        return {
            'content': intro,
            'message_type': 'exam_analysis',
            'metadata': {
                'document_id': document_id,
                'title': title,
                'subject': subject,
                'doc_type': 'examen',
                'resume': analysis.get('resume', ''),
                'themes': analysis.get('themes', []),
                'questions_extraites': analysis.get('questions_extraites', []),
            }
        }
