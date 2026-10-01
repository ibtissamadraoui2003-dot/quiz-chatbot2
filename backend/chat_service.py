"""
chat_service.py
-----------------
Cœur du « chatbot ». Reçoit un message libre de l'élève, devine ce qu'il
veut (un quiz ? un résumé ? ses points faibles ? une question ouverte ?)
et répond en conséquence, en s'appuyant sur les documents déjà envoyés
et sur l'historique de la conversation (stocké en base -> mémoire durable).

La détection d'intention se fait par expressions régulières simples :
pas besoin d'un appel IA supplémentaire pour un périmètre aussi ciblé,
et ça reste 100% prévisible. Les boutons rapides du frontend appellent
directement les mêmes intentions.
"""

import re

from retrieval import top_k_chunks, format_context

RE_EXPLAIN_CONCEPT = re.compile(r'^\s*explique[\s-]?moi\s*[:\-]\s*(.+)$', re.IGNORECASE)
RE_WEAK_POINTS = re.compile(
    r"points?\s+faibles?|je\s+ne\s+comprends\s+pas|n[e\']?\s*ai\s*pas\s*(compris|appris)|"
    r"mes\s+lacunes|ce\s+que\s+je\s+n[e\']?\s*apprends?\s*pas|"
    r"ce\s+que\s+je\s+ne\s+(maitrise|ma[iî]trise)\s+pas",
    re.IGNORECASE
)
RE_QUIZ = re.compile(r"\b(quiz|questionnaire|qcm|teste[\s-]?moi|interroge[\s-]?moi)\b", re.IGNORECASE)
RE_SUMMARY = re.compile(r"r[ée]sum[ée]|points?\s+essentiels?|points?\s+cl[ée]s?|synth[èe]se", re.IGNORECASE)
RE_NUM_QUESTIONS = re.compile(r'(\d{1,2})\s*questions?', re.IGNORECASE)


class ChatService:
    def __init__(self, db, ai, quiz_service):
        self.db = db
        self.ai = ai
        self.quiz_service = quiz_service

    # ------------------------------------------------------------------
    def handle_message(self, content):
        content = (content or '').strip()
        if not content:
            return {'error': 'Message vide.'}

        history_snapshot = self.db.get_recent_messages(limit=6)  # avant d'ajouter le message courant
        user_message = self.db.add_message('user', content, 'text')

        concept_match = RE_EXPLAIN_CONCEPT.match(content)
        if concept_match:
            assistant_message = self._respond_explain_concept(concept_match.group(1).strip())
        elif RE_WEAK_POINTS.search(content):
            assistant_message = self._respond_weak_points()
        elif RE_QUIZ.search(content):
            assistant_message = self._respond_quiz(content)
        elif RE_SUMMARY.search(content):
            assistant_message = self._respond_summary(content)
        else:
            assistant_message = self._respond_general(content, history_snapshot)

        return {'user_message': user_message, 'assistant_messages': [assistant_message]}

    # ------------------------------------------------------------------
    def _respond_explain_concept(self, concept):
        doc_ids = [d['id'] for d in self.db.get_all_documents()]
        explanation = self.quiz_service.explain_concept(concept, doc_ids)
        return self.db.add_message('assistant', explanation, 'text')

    def _respond_weak_points(self):
        stats = self.db.get_concept_stats()
        attempted = [s for s in stats if s['total'] > 0]

        if not attempted:
            return self.db.add_message(
                'assistant',
                "Tu n'as pas encore fait de quiz, donc je n'ai pas encore de points faibles à te signaler. "
                "Envoie-moi un cours et lance un quiz : je pourrai ensuite te dire précisément ce qu'il "
                "faut retravailler !",
                'text'
            )

        attempted.sort(key=lambda s: s['accuracy'])
        weak = [s for s in attempted if s['accuracy'] < 70][:6]
        strong_count = len([s for s in attempted if s['accuracy'] >= 70])

        if weak:
            content = "D'après tes quiz, voici les notions sur lesquelles tu sembles encore hésiter :"
        else:
            content = "Beau travail : tu n'as pas de point vraiment faible pour l'instant sur ce que tu as déjà testé !"

        return self.db.add_message('assistant', content, 'weak_points', {
            'weak': weak,
            'strong_count': strong_count,
        })

    def _respond_quiz(self, content):
        matched = self._find_matching_documents(content)
        pool = matched if matched else self.db.get_all_documents()
        if not pool:
            return self.db.add_message(
                'assistant',
                "Je n'ai encore aucun document pour générer un quiz. Envoie-moi d'abord un PDF de cours !",
                'text'
            )

        num_questions, difficulty = self._parse_quiz_params(content)
        doc_ids = [d['id'] for d in pool][:3]  # limite raisonnable de contexte envoyé à l'IA
        result = self.quiz_service.generate_quiz(doc_ids, num_questions, difficulty)
        if result.get('error'):
            return self.db.add_message('assistant', result['error'], 'text')

        intro = self.quiz_service.build_intro(result, difficulty)
        return self.db.add_message('assistant', intro, 'quiz', {
            'document_ids': doc_ids,
            'questions': result['questions'],
        })

    def _respond_summary(self, content):
        matched = self._find_matching_documents(content, doc_type='cours')
        pool = matched if matched else self.db.get_all_documents('cours')
        if not pool:
            return self.db.add_message(
                'assistant',
                "Je n'ai encore aucun cours à résumer. Envoie-moi un PDF de cours pour commencer !",
                'text'
            )
        doc = pool[0]
        summary = doc.get('summary') or {}
        return self.db.add_message('assistant', f"Voici le résumé de **{doc['title']}** :", 'summary', {
            'document_id': doc['id'],
            'title': doc['title'],
            'subject': doc['subject'],
            'doc_type': 'cours',
            'summary': summary.get('summary', ''),
            'key_points': summary.get('key_points', []),
            'concepts': summary.get('concepts', []),
        })

    def _respond_general(self, content, history_snapshot=None):
        docs = self.db.get_all_documents()
        if not docs:
            return self.db.add_message(
                'assistant',
                "Je n'ai encore aucun cours en mémoire ! Envoie-moi un PDF (cours ou examen passé) pour "
                "que je puisse commencer à t'aider à réviser.",
                'text'
            )

        matched = self._find_matching_documents(content)
        target_docs = matched if matched else docs
        chunk_records = self.db.get_chunks_for_documents([d['id'] for d in target_docs])
        relevant = top_k_chunks(content, chunk_records, k=6)
        context = format_context(relevant, max_chars=5000)

        if not self.ai.available():
            if context:
                text = ("_(Aucune clé IA configurée : extrait brut de tes documents en lien avec ta question)_"
                        f"\n\n{context[:1200]}")
            else:
                text = ("Je n'ai pas de clé API IA configurée dans backend/.env, je ne peux donc pas "
                        "encore répondre librement à des questions.")
            return self.db.add_message('assistant', text, 'text')

        history_text = self._format_recent_history(history_snapshot or [])
        system = (
            "Tu es un tuteur pédagogique bienveillant qui aide un étudiant à réviser à partir des documents "
            "qu'il t'a fournis. Réponds en français, de façon claire et concise (150 mots maximum). "
            "Appuie-toi sur les extraits fournis quand c'est pertinent. Si la réponse ne s'y trouve pas, "
            "dis-le honnêtement avant de répondre au mieux avec tes connaissances générales."
        )
        user = ""
        if context:
            user += f'Extraits de cours pertinents :\n"""\n{context}\n"""\n\n'
        if history_text:
            user += f"Échange récent :\n{history_text}\n\n"
        user += f"Question de l'étudiant : {content}"

        answer = self.ai.complete(system, user, max_tokens=600, temperature=0.6)
        if not answer:
            reason = f" {self.ai.last_error}" if self.ai.last_error else " Peux-tu reformuler ta question ?"
            answer = f"Désolé, je n'ai pas réussi à générer de réponse.{reason}"
        return self.db.add_message('assistant', answer.strip(), 'text')

    # ------------------------------------------------------------------
    def _find_matching_documents(self, content, doc_type=None):
        content_l = content.lower()
        docs = self.db.get_all_documents(doc_type)
        matches = []
        for d in docs:
            title_words = [w for w in re.findall(r"\w+", (d.get('title') or '').lower()) if len(w) > 3]
            subject_l = (d.get('subject') or '').lower()
            if subject_l and len(subject_l) > 3 and subject_l in content_l:
                matches.append(d)
                continue
            if any(w in content_l for w in title_words):
                matches.append(d)
        return matches

    def _parse_quiz_params(self, content):
        match = RE_NUM_QUESTIONS.search(content)
        num_questions = int(match.group(1)) if match else 8
        num_questions = max(3, min(num_questions, 20))

        difficulty = 'moyen'
        if re.search(r'\bfacile', content, re.IGNORECASE):
            difficulty = 'facile'
        elif re.search(r'\bdifficile', content, re.IGNORECASE):
            difficulty = 'difficile'
        return num_questions, difficulty

    def _format_recent_history(self, history):
        lines = []
        for m in history:
            if m['message_type'] != 'text':
                continue
            speaker = "Étudiant" if m['role'] == 'user' else "Toi"
            content = (m['content'] or '')[:300]
            lines.append(f"{speaker} : {content}")
        return "\n".join(lines)
