"""Clé API saisie depuis l'app (page Bienvenue) : on retrouve l'ID et le secret dans un texte collé, on les teste
auprès d'osu!, puis on les écrit dans .env. Personne n'a besoin d'ouvrir ce fichier à la main."""
import os
import re

from .config import ROOT

ENV_PATH = ROOT / ".env"
EXAMPLE_PATH = ROOT / ".env.example"

# Le secret client d'osu! fait 40 caractères (lettres et chiffres) ; l'ID client est un nombre.
_SECRET = re.compile(r"(?<![A-Za-z0-9])[A-Za-z0-9]{40}(?![A-Za-z0-9])")
# Un nombre isolé, sauf un port (« localhost:8080 ») ou un morceau d'adresse (« /12345 »).
_ID = re.compile(r"(?<![A-Za-z0-9:/.])\d{1,10}(?![A-Za-z0-9])")


def parse_keys(text: str) -> tuple[str | None, str | None]:
    """(ID client, secret client) trouvés dans un texte collé, dans n'importe quel ordre ; None pour ce qui manque.

    Marche avec les deux valeurs seules, ou avec tout ce que la page OAuth d'osu! laisse copier autour.
    """
    text = text or ""
    secret = _SECRET.search(text)
    rest = _SECRET.sub(" ", text) if secret else text
    client_id = _ID.search(rest)
    return (client_id.group() if client_id else None, secret.group() if secret else None)


def save_env(values: dict[str, str], path=ENV_PATH) -> None:
    """Écrit ces réglages dans .env (créé depuis .env.example au besoin), sans toucher aux autres lignes."""
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif EXAMPLE_PATH.exists():
        lines = EXAMPLE_PATH.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    todo = dict(values)
    for i, line in enumerate(lines):
        name = line.split("=", 1)[0].strip()
        if "=" in line and not line.lstrip().startswith("#") and name in todo:
            lines[i] = f"{name}={todo.pop(name)}"
    lines += [f"{name}={value}" for name, value in todo.items()]
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    os.environ.update(values)  # pris en compte tout de suite par ce processus
