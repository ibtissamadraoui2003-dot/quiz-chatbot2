"""
pdf_processor.py
-----------------
Extraction et nettoyage du texte d'un PDF (cours ou examen), puis découpage
en « chunks » (paragraphes de taille raisonnable) que le reste de
l'application utilise pour la recherche de contexte et la génération IA.

Aucun appel IA ici : uniquement du traitement de texte.
"""

import os
import re
import logging
from pypdf import PdfReader

# pypdf journalise parfois des dizaines de lignes du type « fontTools is
# required to fully parse... » pour des polices non standards : c'est sans
# gravité (le texte est quand même extrait), mais ça noie le terminal.
logging.getLogger('pypdf').setLevel(logging.ERROR)

# Certains PDF (typiquement générés par LaTeX avec un encodage de police
# particulier) exportent une lettre accentuée comme DEUX caractères séparés :
# le signe diacritique, puis la lettre — par ex. "d´ efinition" au lieu de
# "définition", parfois avec un espace parasite entre les deux. On ne corrige
# ce défaut que s'il est net et répété dans le document (voir _looks_broken),
# pour ne jamais toucher un texte normal qui contiendrait juste, par exemple,
# un backtick de code isolé.
_ACCENT_FIXES = [
    ('`', 'a', 'à'), ('`', 'e', 'è'), ('`', 'u', 'ù'), ('`', 'A', 'À'), ('`', 'E', 'È'),
    ('´', 'a', 'á'), ('´', 'e', 'é'), ('´', 'i', 'í'), ('´', 'o', 'ó'), ('´', 'u', 'ú'),
    ('´', 'A', 'Á'), ('´', 'E', 'É'), ('´', 'I', 'Í'), ('´', 'O', 'Ó'), ('´', 'U', 'Ú'),
    ('^', 'a', 'â'), ('^', 'e', 'ê'), ('^', 'i', 'î'), ('^', 'o', 'ô'), ('^', 'u', 'û'),
    ('^', 'A', 'Â'), ('^', 'E', 'Ê'), ('^', 'I', 'Î'), ('^', 'O', 'Ô'), ('^', 'U', 'Û'),
    ('¨', 'e', 'ë'), ('¨', 'i', 'ï'), ('¨', 'u', 'ü'), ('¨', 'E', 'Ë'), ('¨', 'I', 'Ï'),
    ('¸', 'c', 'ç'), ('¸', 'C', 'Ç'),
]
# Un texte français « normal » contient très peu de backtick/accent isolé qui
# ne soit pas un vrai accent : on déclenche donc la correction dès que le
# motif est net, sur un TAUX (par 1000 caractères) plutôt qu'un nombre brut,
# pour que ça marche aussi bien sur un court extrait que sur un PDF de 200 pages.
_BROKEN_ACCENT_MIN_COUNT = 4
_BROKEN_ACCENT_MIN_RATE = 3.0  # occurrences pour 1000 caractères


def _count_broken_accents(text):
    return sum(
        len(re.findall(re.escape(mark) + r'\s?' + re.escape(letter), text))
        for mark, letter, _ in _ACCENT_FIXES
    )


def fix_broken_accents(text):
    if not text:
        return text
    text = text.replace('\u0131', 'i')  # "ı" (i sans point) : variante vue sur certains PDF
    count = _count_broken_accents(text)
    rate = count / len(text) * 1000
    if count < _BROKEN_ACCENT_MIN_COUNT or rate < _BROKEN_ACCENT_MIN_RATE:
        return text
    for mark, letter, accented in _ACCENT_FIXES:
        text = re.sub(re.escape(mark) + r'\s?' + re.escape(letter), accented, text)
    return text


class PDFProcessor:
    def __init__(self, upload_folder='uploads'):
        self.upload_folder = upload_folder
        os.makedirs(upload_folder, exist_ok=True)

    # ------------------------------------------------------------------
    def save_upload(self, pdf_file, filename):
        """Sauvegarde le fichier uploadé sur disque et renvoie son chemin."""
        filepath = os.path.join(self.upload_folder, filename)
        # Évite d'écraser un fichier existant portant le même nom
        base, ext = os.path.splitext(filepath)
        counter = 1
        while os.path.exists(filepath):
            filepath = f"{base}_{counter}{ext}"
            counter += 1
        pdf_file.save(filepath)
        return filepath

    def extract_text(self, pdf_path):
        """Extrait le texte brut de chaque page d'un PDF."""
        if not os.path.exists(pdf_path):
            print(f"❌ Fichier introuvable : {pdf_path}")
            return "", 0

        try:
            reader = PdfReader(pdf_path)
            num_pages = len(reader.pages)
            text_parts = []
            for i, page in enumerate(reader.pages):
                try:
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_parts.append(page_text)
                except Exception as page_err:
                    print(f"⚠️ Page {i + 1} illisible : {page_err}")
            full_text = "\n\n".join(text_parts)
            print(f"✅ PDF lu : {num_pages} pages, {len(full_text)} caractères extraits")
            return full_text, num_pages
        except Exception as e:
            print(f"❌ Erreur de lecture PDF : {e}")
            return "", 0

    def clean_text(self, text):
        """Supprime numéros de page, en-têtes/pieds de page probables, URLs, espaces superflus."""
        if not text:
            return ""

        lines = text.split('\n')
        cleaned = []
        for line in lines:
            line = line.strip()
            if not line or len(line) < 3:
                continue
            if line.isdigit():                       # numéro de page seul
                continue
            if '@' in line or 'http://' in line or 'https://' in line:
                continue
            if line.count('/') >= 3:                 # ligne type date jj/mm/aaaa répétée
                continue
            if line.lower().startswith('page ') and len(line) < 20:
                continue
            cleaned.append(line)

        text = '\n'.join(cleaned)
        text = re.sub(r'[ \t]+', ' ', text)
        text = re.sub(r'\n{3,}', '\n\n', text)
        return text.strip()

    def split_into_chunks(self, text, chunk_size=1200, overlap=150):
        """Découpe le texte en chunks par paragraphe, avec un léger chevauchement
        pour ne pas couper une idée en plein milieu."""
        if not text or not text.strip():
            return []

        paragraphs = [p.strip() for p in text.split('\n\n') if p.strip()]
        # Si le PDF n'a presque pas de doubles sauts de ligne, on retombe sur les phrases
        if len(paragraphs) <= 1 and len(text) > chunk_size:
            paragraphs = [p.strip() for p in re.split(r'(?<=[.!?])\s+', text) if p.strip()]

        chunks = []
        current = ""
        for para in paragraphs:
            if len(current) + len(para) + 1 <= chunk_size:
                current = f"{current}\n{para}".strip()
            else:
                if current:
                    chunks.append(current)
                    # chevauchement : on garde la fin du chunk précédent pour le contexte
                    current = current[-overlap:] + "\n" + para if overlap else para
                else:
                    current = para
                # si un seul paragraphe dépasse déjà chunk_size, on le coupe brut
                while len(current) > chunk_size * 1.5:
                    chunks.append(current[:chunk_size])
                    current = current[chunk_size - overlap:]
        if current.strip():
            chunks.append(current.strip())

        print(f"📝 Texte découpé en {len(chunks)} extraits")
        return chunks

    def process(self, pdf_path):
        """Pipeline complet : extraction -> nettoyage -> découpage."""
        raw_text, num_pages = self.extract_text(pdf_path)
        if not raw_text:
            return [], 0
        raw_text = fix_broken_accents(raw_text)
        clean = self.clean_text(raw_text)
        chunks = self.split_into_chunks(clean)
        return chunks, num_pages

    @staticmethod
    def sample_for_overview(chunks, max_chars=9000):
        """Échantillonne des extraits sur TOUT le document (pas juste le début)
        afin qu'un résumé reflète l'ensemble du cours, même s'il est long."""
        if not chunks:
            return ""
        full = "\n\n".join(chunks)
        if len(full) <= max_chars:
            return full

        # Prend un peu partout dans le document plutôt que juste le début
        n = len(chunks)
        step = max(1, n // 12)
        sampled = chunks[::step]
        text = "\n\n[...]\n\n".join(sampled)
        return text[:max_chars]
