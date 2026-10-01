"""
ai_provider.py
---------------
Point d'entrée UNIQUE vers le modèle de langage (IA), pour ne pas dupliquer
la logique d'appel API dans chaque module (résumé, quiz, chat, explications...).

Détecte automatiquement quelle clé API a été renseignée dans backend/.env :
- GEMINI_API_KEY  -> Google Gemini (SDK « google-genai »)
- OPENAI_API_KEY  -> OpenAI (SDK « openai »)
Si les deux sont présentes, Gemini est utilisé en priorité.

Si aucune clé n'est trouvée, l'application continue de fonctionner en
« mode dégradé » (upload, résumé basique, quiz de secours) plutôt que de
planter : available() renvoie alors False et chaque module sait proposer
un comportement de repli.

IMPORTANT — les modèles changent souvent chez les fournisseurs : un modèle
est en général retiré environ un an après sa sortie. Si un appel échoue avec
« modèle introuvable », il suffit de renseigner un modèle actuel dans
backend/.env (GEMINI_MODEL ou OPENAI_MODEL), sans toucher au code.

Les SDK sont importés à l'intérieur de __init__ : si le paquet d'un
fournisseur que tu n'utilises pas n'est pas installé, l'application
démarre quand même.
"""

import os
import json
import time
from dotenv import load_dotenv

load_dotenv()

# Modèles par défaut (vérifiés dans les documentations officielles en sept. 2026).
DEFAULT_GEMINI_MODEL = 'gemini-3.8-flash'
DEFAULT_OPENAI_MODEL = 'gpt-5.4-mini'

# Erreurs passagères (le service est momentanément débordé) : ça vaut le coup
# de réessayer automatiquement quelques secondes après, sans déranger l'élève.
_TRANSIENT_MARKERS = (
    '503', 'unavailable', 'high demand', 'overloaded', 'server_error',
    '429', 'quota', 'rate limit', 'rate_limit', 'resource_exhausted', 'too many requests',
)


def is_transient_error(exc):
    return any(k in str(exc).lower() for k in _TRANSIENT_MARKERS)


def explain_error(exc, provider, model):
    """Traduit une erreur technique du fournisseur en conseil clair pour l'élève."""
    msg = str(exc)
    low = msg.lower()
    var = 'GEMINI_MODEL' if provider == 'gemini' else 'OPENAI_MODEL'
    label = 'Gemini' if provider == 'gemini' else 'OpenAI'

    if any(k in low for k in ('not found', 'no longer available', 'model_not_found',
                              'does not exist', 'is not supported', '404')):
        return (f"Le modèle « {model} » est introuvable ou a été retiré par le fournisseur. "
                f"Renseigne un modèle actuel dans backend/.env ({var}=...).")
    if any(k in low for k in ('503', 'unavailable', 'high demand', 'overloaded', 'server_error')):
        return (f"Le service {label} est temporairement surchargé (déjà réessayé plusieurs fois "
                f"automatiquement, sans succès). Ce n'est pas lié à ta config : patiente une minute "
                f"puis réessaie.")
    if any(k in low for k in ('429', 'quota', 'rate limit', 'rate_limit',
                              'resource_exhausted', 'too many requests')):
        return ("Quota ou limite de requêtes atteint chez le fournisseur d'IA : "
                "patiente un instant puis réessaie (ou choisis un autre modèle dans backend/.env).")
    if any(k in low for k in ('401', '403', 'api key', 'api_key', 'permission',
                              'unauthorized', 'invalid_api_key', 'authentication')):
        return "Clé API refusée par le fournisseur : vérifie la clé dans backend/.env."
    return f"Erreur du fournisseur d'IA : {msg[:200]}"


class AIProvider:
    def __init__(self):
        self.provider = None
        self.client = None
        self.model_name = None
        self.last_error = None      # dernier problème rencontré (texte lisible)
        self._types = None          # module google.genai.types (Gemini uniquement)
        self._selftest = None       # résultat du test de connexion, mis en cache

        gemini_key = os.getenv('GEMINI_API_KEY', '').strip()
        openai_key = os.getenv('OPENAI_API_KEY', '').strip()

        if gemini_key and not gemini_key.startswith('votre_'):
            try:
                from google import genai
                from google.genai import types
                self.client = genai.Client(api_key=gemini_key)
                self._types = types
                self.model_name = os.getenv('GEMINI_MODEL', '').strip() or DEFAULT_GEMINI_MODEL
                self.provider = 'gemini'
                print(f"✅ IA active : Google Gemini ({self.model_name})")
            except ImportError:
                print("❌ Le paquet « google-genai » n'est pas installé : pip install -r requirements.txt")
            except Exception as e:
                print(f"❌ Impossible d'initialiser Gemini : {e}")

        if not self.provider and openai_key and not openai_key.startswith('votre_'):
            try:
                from openai import OpenAI
                self.client = OpenAI(api_key=openai_key)
                self.model_name = os.getenv('OPENAI_MODEL', '').strip() or DEFAULT_OPENAI_MODEL
                self.provider = 'openai'
                print(f"✅ IA active : OpenAI ({self.model_name})")
            except ImportError:
                print("❌ Le paquet « openai » n'est pas installé : pip install -r requirements.txt")
            except Exception as e:
                print(f"❌ Impossible d'initialiser OpenAI : {e}")

        if not self.provider:
            print("⚠️  Aucune clé API IA valide (GEMINI_API_KEY ou OPENAI_API_KEY) dans backend/.env")
            print("    L'application fonctionne en mode dégradé (sans génération IA).")

    # ------------------------------------------------------------------
    def available(self):
        return self.provider is not None

    def status(self):
        return {
            'available': self.available(),
            'provider': self.provider,
            'model': self.model_name,
            'last_error': self.last_error,
        }

    def self_test(self):
        """Petit appel de vérification (clé + modèle valides ?). Un SUCCÈS est mis
        en cache (pas la peine de reconsommer du quota à chaque page). Un ÉCHEC
        n'est PAS mis en cache : il peut être temporaire (ex. service surchargé),
        donc on retente à la prochaine visite plutôt que de rester bloqué en rouge."""
        if not self.available():
            return {'ok': False, 'message': "Aucune clé API configurée."}
        if self._selftest is not None:
            return self._selftest
        text = self.complete("Réponds uniquement par le mot OK.", "Test de connexion.", max_tokens=20)
        if text:
            self._selftest = {'ok': True, 'message': 'OK'}
            return self._selftest
        return {'ok': False, 'message': self.last_error or "Échec inconnu."}

    # ------------------------------------------------------------------
    def complete(self, system_prompt, user_prompt, json_mode=False, max_tokens=2000, temperature=None,
                 max_attempts=3, retry_delay=1.5):
        """Appelle le LLM et renvoie le texte de la réponse, ou None en cas d'échec
        (dans ce cas, self.last_error explique pourquoi).

        Les erreurs passagères (503 « surchargé », 429 « quota ») sont réessayées
        automatiquement quelques secondes après, avant d'abandonner : ce sont les
        erreurs les plus fréquentes chez les fournisseurs d'IA et elles se
        résolvent presque toujours d'elles-mêmes très vite.

        `temperature` est accepté mais volontairement NON transmis : les modèles
        récents (Gemini 3, GPT-5) imposent ou recommandent la valeur par défaut."""
        if not self.available():
            return None

        for attempt in range(1, max_attempts + 1):
            try:
                if self.provider == 'gemini':
                    text = self._complete_gemini(system_prompt, user_prompt, json_mode, max_tokens)
                else:
                    text = self._complete_openai(system_prompt, user_prompt, json_mode, max_tokens)
            except Exception as e:
                if attempt < max_attempts and is_transient_error(e):
                    print(f"⏳ {self.provider} momentanément indisponible (tentative {attempt}/{max_attempts}), "
                          f"nouvel essai dans {retry_delay:.0f}s...")
                    time.sleep(retry_delay)
                    retry_delay *= 2
                    continue
                self.last_error = explain_error(e, self.provider, self.model_name)
                print(f"❌ Erreur d'appel IA ({self.provider}) : {e}")
                return None

            if not text or not text.strip():
                self.last_error = "Le modèle a renvoyé une réponse vide (filtre de sécurité ou limite atteinte)."
                print(f"⚠️  {self.last_error}")
                return None

            self.last_error = None
            return text
        return None

    def _generous_limit(self, max_tokens):
        # Les modèles récents « réfléchissent » avant de répondre et ces jetons de
        # réflexion comptent dans la limite de sortie : avec une limite trop
        # serrée, la réponse pourrait être vide. Les prompts demandent déjà des
        # réponses courtes, cette marge n'allonge donc pas les textes.
        return max(max_tokens * 3, 3000)

    def _complete_gemini(self, system_prompt, user_prompt, json_mode, max_tokens):
        config_kwargs = {
            'system_instruction': system_prompt,
            'max_output_tokens': self._generous_limit(max_tokens),
        }
        if json_mode:
            config_kwargs['response_mime_type'] = 'application/json'
        response = self.client.models.generate_content(
            model=self.model_name,
            contents=user_prompt,
            config=self._types.GenerateContentConfig(**config_kwargs),
        )
        return response.text

    def _complete_openai(self, system_prompt, user_prompt, json_mode, max_tokens):
        kwargs = {
            'model': self.model_name,
            'messages': [
                {'role': 'system', 'content': system_prompt},
                {'role': 'user', 'content': user_prompt},
            ],
            'max_completion_tokens': self._generous_limit(max_tokens),
        }
        if json_mode:
            kwargs['response_format'] = {'type': 'json_object'}
        try:
            response = self.client.chat.completions.create(**kwargs)
        except Exception as e:
            # Certains anciens modèles n'acceptent que l'ancien nom du paramètre.
            if 'max_completion_tokens' in str(e):
                kwargs['max_tokens'] = kwargs.pop('max_completion_tokens')
                response = self.client.chat.completions.create(**kwargs)
            else:
                raise
        return response.choices[0].message.content

    # ------------------------------------------------------------------
    def complete_json(self, system_prompt, user_prompt, max_tokens=2000, temperature=None):
        """Comme complete(), mais renvoie directement l'objet JSON décodé.
        Renvoie None si l'IA est indisponible ou si le parsing échoue."""
        raw = self.complete(system_prompt, user_prompt, json_mode=True,
                             max_tokens=max_tokens, temperature=temperature)
        if not raw:
            return None
        parsed = self._extract_json(raw)
        if parsed is None:
            self.last_error = "L'IA n'a pas renvoyé un JSON exploitable."
        return parsed

    @staticmethod
    def _extract_json(raw):
        raw = re_sub_fences(raw)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass
        for open_ch, close_ch in (('{', '}'), ('[', ']')):
            start = raw.find(open_ch)
            end = raw.rfind(close_ch) + 1
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end])
                except json.JSONDecodeError:
                    continue
        print("❌ Réponse IA non-JSON, impossible à parser")
        return None


def re_sub_fences(text):
    """Retire les balises de bloc de code ```json ... ``` ou ``` ... ``` si présentes."""
    text = text.strip()
    if text.startswith('```'):
        first_newline = text.find('\n')
        if first_newline != -1:
            text = text[first_newline + 1:]
    if text.endswith('```'):
        text = text[:-3]
    return text.strip()
