"""
retrieval.py
-------------
« Mémoire de contenu » légère : plutôt que d'envoyer tout un cours entier
à chaque question (trop long, trop coûteux), on ne renvoie à l'IA que les
extraits (chunks) les plus pertinents par rapport à la question posée.

Implémentation volontairement simple (comptage de mots-clés partagés,
sans base vectorielle ni service d'embeddings externe) : suffisante pour
des cours de quelques dizaines de pages, sans dépendance ni coût supplémentaire.
"""

import re
from collections import Counter

STOPWORDS_FR = {
    'le', 'la', 'les', 'un', 'une', 'des', 'de', 'du', 'au', 'aux', 'et', 'ou',
    'est', 'en', 'que', 'qui', 'quoi', 'dont', 'où', 'a', 'à', 'dans', 'pour',
    'par', 'sur', 'avec', 'sans', 'ce', 'cet', 'cette', 'ces', 'son', 'sa',
    'ses', 'leur', 'leurs', 'mon', 'ma', 'mes', 'ton', 'ta', 'tes', 'notre',
    'nos', 'votre', 'vos', 'il', 'elle', 'ils', 'elles', 'on', 'nous', 'vous',
    'je', 'tu', 'se', 'ne', 'pas', 'plus', 'moins', 'très', 'bien', 'comme',
    'donc', 'mais', 'or', 'ni', 'car', 'si', 'tout', 'tous', 'toute', 'toutes',
    'être', 'avoir', 'fait', 'faire', 'c', 'd', 'l', 'qu', 's', 'n', 'j', 'm',
    't', 'y', 'eu', 'été', 'étaient', 'sont', 'était', 'peut', 'peux', 'quel',
    'quelle', 'quels', 'quelles', 'expliquer', 'explique', 'explique-moi',
}


def tokenize(text):
    text = text.lower()
    words = re.findall(r"[a-zàâäéèêëïîôöùûüçœ0-9]+", text)
    return [w for w in words if w not in STOPWORDS_FR and len(w) > 2]


def _score(query_tokens, chunk_tokens):
    if not chunk_tokens:
        return 0.0
    counts = Counter(chunk_tokens)
    score = 0.0
    for t in query_tokens:
        if t in counts:
            score += counts[t] / (1 + 0.02 * len(chunk_tokens))
    return score


def top_k_chunks(query, chunk_records, k=6):
    """chunk_records : liste de dicts contenant au moins la clé 'content'.
    Renvoie les k extraits les plus pertinents pour `query`.
    Si la question ne partage aucun mot avec les extraits (question très
    générale), on retombe sur les premiers extraits plutôt que de renvoyer
    une liste vide."""
    if not chunk_records:
        return []

    query_tokens = tokenize(query)
    if not query_tokens:
        return chunk_records[:k]

    scored = []
    for rec in chunk_records:
        tokens = tokenize(rec.get('content', ''))
        scored.append((_score(query_tokens, tokens), rec))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    relevant = [rec for s, rec in scored if s > 0]
    if not relevant:
        return chunk_records[:k]
    return relevant[:k]


def format_context(chunks, max_chars=6000):
    """Assemble une liste d'extraits (avec leur document source) en un seul
    bloc de texte lisible par l'IA, avec citation de la source."""
    parts = []
    total = 0
    for c in chunks:
        title = c.get('doc_title', 'Document')
        piece = f"[Extrait de « {title} »]\n{c['content']}"
        if total + len(piece) > max_chars:
            break
        parts.append(piece)
        total += len(piece)
    return "\n\n---\n\n".join(parts)
