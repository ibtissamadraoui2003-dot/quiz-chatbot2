"""
quiz_service.py
-----------------
Génération des quiz (QCM), correction des réponses de l'élève, et
explications pédagogiques — aussi bien pour une mauvaise réponse ponctuelle
que pour un concept entier sur lequel l'élève est en difficulté.
"""

import re
import random

from retrieval import top_k_chunks, format_context


class QuizService:
    def __init__(self, db, ai):
        self.db = db
        self.ai = ai

    # ------------------------------------------------------------------
    def generate_quiz(self, document_ids, num_questions=8, difficulty='moyen'):
        chunk_records = self.db.get_chunks_for_documents(document_ids)
        if not chunk_records:
            return {'error': "Aucun contenu disponible pour générer un quiz sur ce(s) document(s)."}

        documents = [self.db.get_document(did) for did in document_ids]
        documents = [d for d in documents if d]
        known_concepts = []
        for d in documents:
            if d.get('summary'):
                known_concepts.extend(d['summary'].get('concepts', []) or d['summary'].get('themes', []))

        sample_text = self._sample_combined(chunk_records, max_chars=8000)
        questions = []
        degraded_reason = None
        if self.ai.available():
            questions = self._generate_with_ai(sample_text, num_questions, difficulty, known_concepts)
            if not questions:
                degraded_reason = self.ai.last_error or "L'IA n'a pas produit de questions exploitables"
        else:
            degraded_reason = "Aucune clé API IA n'est configurée dans backend/.env"
        if not questions:
            # Questions de secours (basiques), seulement si l'IA n'a rien donné du tout :
            # on ne mélange pas de vraies questions et des questions génériques.
            questions = self._fallback_questions(chunk_records, num_questions, difficulty)

        questions = questions[:num_questions]
        saved = []
        primary_doc_id = document_ids[0] if document_ids else None
        for q in questions:
            concept = self._match_concept(q.get('concept', ''), known_concepts)
            qid = self.db.add_question(
                document_id=primary_doc_id,
                question=q['question'],
                options=q['options'],
                correct_index=q['correct_index'],
                explanation=q.get('explanation', ''),
                concept=concept,
                difficulty=difficulty,
                source='genere',
            )
            saved.append(self.db.get_question(qid))

        return {'questions': saved, 'documents': documents, 'degraded_reason': degraded_reason}

    def build_intro(self, result, difficulty):
        """Phrase d'introduction du message de quiz (indique si les questions sont simplifiées)."""
        titles = ', '.join(f"**{d['title']}**" for d in result['documents'])
        intro = f"Voici un quiz de {len(result['questions'])} questions ({difficulty}) sur {titles} :"
        if result.get('degraded_reason'):
            intro += f"\n\n_({result['degraded_reason']} : questions simplifiées.)_"
        return intro

    def _sample_combined(self, chunk_records, max_chars=8000):
        n = len(chunk_records)
        if n == 0:
            return ""
        step = max(1, n // 20)
        sampled = chunk_records[::step]
        text = "\n\n".join(f"[{c.get('doc_title', 'Document')}] {c['content']}" for c in sampled)
        return text[:max_chars]

    def _generate_with_ai(self, sample_text, num_questions, difficulty, known_concepts):
        system = (
            "Tu es un expert en pédagogie qui crée des questionnaires à choix multiples (QCM) "
            "universitaires. Tu réponds UNIQUEMENT avec un objet JSON valide, sans texte autour."
        )
        concepts_hint = ""
        if known_concepts:
            concepts_hint = f"\nNotions déjà identifiées dans ce cours : {', '.join(known_concepts[:15])}."

        user = f'''À partir du texte suivant, génère {num_questions} questions de quiz de difficulté "{difficulty}".
{concepts_hint}

TEXTE :
"""
{sample_text}
"""

RÈGLES :
1. Chaque question a EXACTEMENT 4 options, une seule correcte.
2. Teste la compréhension, pas la simple mémorisation.
3. Les 3 mauvaises options doivent être plausibles (pas absurdes).
4. Associe à chaque question un "concept" court (la notion précise qu'elle teste).
5. Donne une explication pédagogique de la bonne réponse.

Réponds avec un JSON de cette forme exacte :
{{
  "questions": [
    {{
      "question": "texte de la question",
      "options": ["option A", "option B", "option C", "option D"],
      "correct_index": 0,
      "explanation": "explication de la réponse correcte",
      "concept": "notion testée"
    }}
  ]
}}
Écris en français.'''

        result = self.ai.complete_json(system, user, max_tokens=2500)
        if not result:
            return []

        questions = []
        for q in result.get('questions', []):
            if (q.get('question') and isinstance(q.get('options'), list) and
                    len(q['options']) == 4 and isinstance(q.get('correct_index'), int) and
                    0 <= q['correct_index'] < 4):
                questions.append(q)
        return questions

    def _fallback_questions(self, chunk_records, num_questions, difficulty):
        """Utilisé si l'IA est indisponible ou n'a pas produit assez de questions.
        Moins pertinent pédagogiquement, mais garde l'application fonctionnelle."""
        sentences = []
        for rec in chunk_records:
            for s in re.split(r'(?<=[.!?])\s+', rec['content']):
                s = s.strip()
                if 30 < len(s) < 220:
                    sentences.append(s)
        if not sentences:
            return []

        random.shuffle(sentences)
        questions = []
        for i in range(min(num_questions, len(sentences))):
            s = sentences[i]
            questions.append({
                'question': f"D'après le document, laquelle de ces affirmations est correcte ?",
                'options': [
                    s if len(s) < 150 else s[:147] + '...',
                    "Le document affirme le contraire de cette idée",
                    "Cette information n'apparaît pas dans le document",
                    "Le document reste ambigu sur ce point",
                ],
                'correct_index': 0,
                'explanation': "Cette information est reprise directement du document fourni.",
                'concept': 'Général',
                'difficulty': difficulty,
            })
        return questions

    def _match_concept(self, ai_concept, known_concepts):
        if not ai_concept:
            return known_concepts[0] if known_concepts else 'Général'
        ai_concept_norm = ai_concept.strip().lower()
        for kc in known_concepts:
            if kc.lower() == ai_concept_norm or kc.lower() in ai_concept_norm or ai_concept_norm in kc.lower():
                return kc  # réutilise l'orthographe déjà connue pour un suivi cohérent
        return ai_concept.strip()[:60] or 'Général'

    # ------------------------------------------------------------------
    def record_answer(self, question_id, selected_index):
        question = self.db.get_question(question_id)
        if not question:
            return {'error': 'Question introuvable.'}

        is_correct = (selected_index == question['correct_index'])
        self.db.record_attempt(question_id, selected_index, is_correct)

        explanation = question.get('explanation', '')
        if not is_correct:
            tailored = self._explain_wrong_answer(question, selected_index)
            if tailored:
                explanation = tailored

        return {
            'is_correct': is_correct,
            'correct_index': question['correct_index'],
            'correct_answer': question['options'][question['correct_index']],
            'explanation': explanation,
            'concept': question.get('concept', 'Général'),
        }

    def _explain_wrong_answer(self, question, selected_index):
        if not self.ai.available():
            return None
        options = question['options']
        user_answer = options[selected_index] if 0 <= selected_index < len(options) else "(aucune réponse valide)"
        system = "Tu es un tuteur patient et bienveillant qui aide un étudiant à comprendre son erreur, sans le décourager."
        user = f'''Question : {question['question']}
Options : {', '.join(f"{chr(65+i)}) {o}" for i, o in enumerate(options))}
Réponse correcte : {options[question['correct_index']]}
Réponse donnée par l'étudiant : {user_answer}

En 3 à 4 phrases maximum, explique pourquoi la réponse correcte est la bonne et, si possible, pourquoi l'option choisie par l'étudiant est une erreur fréquente. Sois encourageant. Réponds en français, sans formule d'introduction inutile.'''
        result = self.ai.complete(system, user, max_tokens=350, temperature=0.5)
        return result.strip() if result else None

    # ------------------------------------------------------------------
    def explain_concept(self, concept, document_ids=None):
        """Explication pédagogique d'un concept sur lequel l'élève est en
        difficulté, en s'appuyant sur les extraits de cours qui en parlent."""
        chunk_records = self.db.get_chunks_for_documents(document_ids) if document_ids else []
        relevant = top_k_chunks(concept, chunk_records, k=4) if chunk_records else []
        context = format_context(relevant, max_chars=3500) if relevant else ""

        if not self.ai.available():
            if context:
                return (f"Voici ce que dit ton cours à propos de **{concept}** "
                        f"(IA indisponible, extrait brut de ton cours) :\n\n{context}")
            return (f"Je n'ai pas de clé API IA configurée dans backend/.env, donc je ne peux pas "
                    f"générer d'explication détaillée sur **{concept}** pour le moment.")

        system = "Tu es un tuteur pédagogique patient qui explique un point de cours à un étudiant qui a des difficultés dessus."
        context_block = f'\n\nExtraits de cours en lien avec ce concept :\n"""\n{context}\n"""' if context else ""
        user = f'''L'étudiant a du mal avec la notion suivante : "{concept}".{context_block}

Explique cette notion clairement, avec :
1. Une explication simple en 2-3 phrases.
2. Un exemple concret.
3. Une astuce courte pour bien la retenir.

Sois encourageant, sans être condescendant. Réponds en français, en 150 mots maximum, sans formule d'introduction inutile.'''
        result = self.ai.complete(system, user, max_tokens=450, temperature=0.6)
        if result:
            return result.strip()
        reason = f" {self.ai.last_error}" if self.ai.last_error else ""
        return f"Je n'ai pas pu générer d'explication pour **{concept}** pour le moment.{reason}"
