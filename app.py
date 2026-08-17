"""
FOOTCLUBSENEGAL.SN — Prototype d'essai
======================================

Banc d'essai des règles métier de la plateforme, en un seul fichier.

Ce prototype sert à VÉRIFIER LA LOGIQUE, pas à être mis en production :
  - base SQLite locale, créée et peuplée automatiquement au premier lancement
  - mots de passe en clair simplement hachés, sans jeton de session
  - pas de cloisonnement réseau, pas de stockage sécurisé des documents

La plateforme réelle (FastAPI + PostgreSQL + Next.js) reprend exactement les
mêmes règles, avec la sécurité et la robustesse qui manquent ici.

Lancement :
    pip install streamlit pandas
    streamlit run app.py
"""
import hashlib
import sqlite3
import uuid
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

DB = "fcs_demo.db"

# ══════════════════════════════════════════════════════════════════════
#  RÈGLES MÉTIER PAR DÉFAUT
#  Ce sont les 8 points à faire trancher par la LSFP. Modifiables dans
#  l'application, écran « Règles » du portail Ligue.
# ══════════════════════════════════════════════════════════════════════

DEFAULT_RULES = {
    "yellow_cards_threshold": ("5", "Cartons jaunes cumulés déclenchant une suspension"),
    "yellow_suspension_matches": ("1", "Matchs de suspension pour cumul d'avertissements"),
    "red_direct_suspension_matches": ("2", "Matchs de suspension pour carton rouge direct"),
    "red_cumulative_suspension_matches": ("1", "Matchs de suspension pour rouge par cumul"),
    "reset_yellows_after_suspension": ("1", "Remettre le compteur de jaunes à zéro (1=oui, 0=non)"),
    "min_players_sheet": ("11", "Nombre minimum de joueurs sur la feuille"),
    "max_players_sheet": ("18", "Nombre maximum de joueurs sur la feuille"),
    "claim_deadline_hours": ("48", "Délai de dépôt d'une réclamation, en heures"),
    "medical_validity_months": ("12", "Durée de validité du certificat médical, en mois"),
}

REQUIRED_DOCS = ["Pièce d'identité", "Photo", "Certificat médical", "Contrat signé"]

YELLOW_REASONS = [
    "Comportement antisportif", "Contestation de décision", "Faute tactique",
    "Retard à la reprise du jeu", "Non-respect de la distance",
]
RED_REASONS = [
    "Faute grossière", "Comportement violent", "Crachat",
    "Main volontaire empêchant un but", "Anéantissement d'une occasion de but",
    "Propos injurieux",
]
POSITIONS = ["Gardien", "Défenseur", "Milieu", "Attaquant"]
CLAIM_TYPES = ["Qualification", "Identité", "Carton", "Score", "Arbitrage", "Organisation"]


# ══════════════════════════════════════════════════════════════════════
#  BASE DE DONNÉES
# ══════════════════════════════════════════════════════════════════════

def conn():
    c = sqlite3.connect(DB, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def uid():
    return str(uuid.uuid4())[:8]


def pw(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def init_db():
    c = conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS rules (
        key TEXT PRIMARY KEY, value TEXT, label TEXT);

    CREATE TABLE IF NOT EXISTS clubs (
        id TEXT PRIMARY KEY, name TEXT, short_name TEXT, city TEXT, stadium TEXT);

    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY, email TEXT UNIQUE, password TEXT,
        full_name TEXT, role TEXT, club_id TEXT);

    CREATE TABLE IF NOT EXISTS players (
        id TEXT PRIMARY KEY, club_id TEXT, first_name TEXT, last_name TEXT,
        birth_date TEXT, nationality TEXT, position TEXT, shirt_number INTEGER,
        contract_start TEXT, contract_end TEXT,
        salary INTEGER DEFAULT 0, match_bonus INTEGER DEFAULT 0, goal_bonus INTEGER DEFAULT 0,
        medical_expiry TEXT,
        docs TEXT DEFAULT '',                -- pièces déposées, séparées par ;
        dossier_status TEXT DEFAULT 'BROUILLON',
        dossier_reason TEXT,
        licence_no TEXT, licence_status TEXT);

    CREATE TABLE IF NOT EXISTS matches (
        id TEXT PRIMARY KEY, matchday INTEGER, home_club TEXT, away_club TEXT,
        kickoff TEXT, stadium TEXT, access_code TEXT UNIQUE,
        status TEXT DEFAULT 'PROGRAMME', home_score INTEGER, away_score INTEGER,
        referee_id TEXT, referee_status TEXT DEFAULT 'DESIGNE');

    CREATE TABLE IF NOT EXISTS sheets (
        id TEXT PRIMARY KEY, match_id TEXT, club_id TEXT,
        status TEXT DEFAULT 'BROUILLON', reason TEXT);

    CREATE TABLE IF NOT EXISTS sheet_players (
        id TEXT PRIMARY KEY, sheet_id TEXT, player_id TEXT,
        starter INTEGER DEFAULT 0, captain INTEGER DEFAULT 0, checked INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, match_id TEXT, club_id TEXT, player_id TEXT,
        related_id TEXT, type TEXT, minute INTEGER, card_kind TEXT,
        reason TEXT, own_goal INTEGER DEFAULT 0, penalty INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS signatures (
        id TEXT PRIMARY KEY, match_id TEXT, role TEXT, signer TEXT, signed_at TEXT);

    CREATE TABLE IF NOT EXISTS suspensions (
        id TEXT PRIMARY KEY, player_id TEXT, origin_match TEXT,
        total INTEGER, remaining INTEGER, reason TEXT,
        source TEXT DEFAULT 'AUTOMATIQUE', status TEXT DEFAULT 'ACTIVE', created_at TEXT);

    CREATE TABLE IF NOT EXISTS yellows (
        player_id TEXT PRIMARY KEY, count INTEGER DEFAULT 0);

    CREATE TABLE IF NOT EXISTS claims (
        id TEXT PRIMARY KEY, match_id TEXT, club_id TEXT, type TEXT, body TEXT,
        status TEXT DEFAULT 'DEPOSEE', filed_at TEXT, deadline TEXT,
        reply TEXT, decision TEXT, reasoning TEXT);

    CREATE TABLE IF NOT EXISTS audit (
        id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT, who TEXT,
        action TEXT, detail TEXT, reason TEXT);
    """)

    if not c.execute("SELECT 1 FROM rules LIMIT 1").fetchone():
        for key, (value, label) in DEFAULT_RULES.items():
            c.execute("INSERT INTO rules VALUES (?,?,?)", (key, value, label))

    if not c.execute("SELECT 1 FROM clubs LIMIT 1").fetchone():
        seed(c)

    c.commit()
    c.close()


def seed(c):
    """Peuple la base : 4 clubs, effectifs, arbitre, deux matchs."""
    clubs = [
        ("ASC Jaraaf", "JAR", "Dakar", "Stade Iba Mar Diop"),
        ("Teungueth FC", "TFC", "Rufisque", "Stade Ngalandou Diouf"),
        ("Casa Sports", "CAS", "Ziguinchor", "Stade Aline Sitoé Diatta"),
        ("Génération Foot", "GF", "Déni Biram Ndao", "Stade Déni Biram Ndao"),
    ]
    prenoms = ["Moussa", "Ibrahima", "Cheikh", "Abdoulaye", "Ousmane", "Mamadou",
               "Alioune", "Babacar", "Modou", "Pape", "Serigne", "Lamine",
               "Amadou", "Assane", "Boubacar", "Malick", "Souleymane", "Khadim"]
    noms = ["Diop", "Ndiaye", "Fall", "Sarr", "Gueye", "Sow", "Ba", "Diallo",
            "Faye", "Seck", "Camara", "Cissé", "Sy", "Mbaye", "Touré", "Kane",
            "Thiam", "Niang"]
    postes = ["Gardien"] + ["Défenseur"] * 4 + ["Milieu"] * 4 + ["Attaquant"] * 3 + \
             ["Gardien", "Défenseur", "Milieu", "Attaquant", "Milieu", "Attaquant"]

    club_ids = []
    # Les dates sont calculées par rapport au jour du lancement, afin que la
    # démonstration fonctionne quelle que soit la date à laquelle on l'ouvre.
    contract_start = (date.today() - timedelta(days=30)).isoformat()
    contract_end = (date.today() + timedelta(days=400)).isoformat()

    for i, (name, short, city, stadium) in enumerate(clubs):
        cid = uid()
        club_ids.append(cid)
        c.execute("INSERT INTO clubs VALUES (?,?,?,?,?)", (cid, name, short, city, stadium))
        c.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
                  (uid(), f"{short.lower()}@club.sn", pw("club123"),
                   f"Secrétariat {name}", "CLUB", cid))

        for j in range(18):
            # Les deux premiers clubs ont des dossiers déjà validés, pour pouvoir
            # composer une feuille immédiatement. Les autres sont en brouillon.
            valide = i < 2
            docs = ";".join(REQUIRED_DOCS) if valide else ";".join(REQUIRED_DOCS[:2])
            c.execute("""INSERT INTO players
                (id, club_id, first_name, last_name, birth_date, nationality,
                 position, shirt_number, contract_start, contract_end,
                 salary, match_bonus, goal_bonus, medical_expiry, docs,
                 dossier_status, licence_no, licence_status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (uid(), cid,
                 prenoms[(i * 4 + j) % len(prenoms)],
                 noms[(i * 6 + j) % len(noms)],
                 f"{1996 + (j % 8)}-{1 + (j % 12):02d}-{1 + (j % 28):02d}",
                 "Sénégalaise", postes[j % len(postes)], j + 1,
                 contract_start, contract_end,
                 150000 + j * 20000, 25000, 15000,
                 (date.today() + timedelta(days=200)).isoformat(),
                 docs,
                 "VALIDE" if valide else "BROUILLON",
                 f"SN-2027-L1-{i * 18 + j + 1:06d}" if valide else None,
                 "ACTIVE" if valide else None))

    # Utilisateurs institutionnels
    c.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
              (uid(), "ligue@lsfp.sn", pw("ligue123"), "Agent LSFP", "LIGUE", None))
    ref_id = uid()
    c.execute("INSERT INTO users VALUES (?,?,?,?,?,?)",
              (ref_id, "arbitre@fsf.sn", pw("arbitre123"),
               "Maguette Ndiaye", "ARBITRE", None))

    # Deux matchs, dont un aujourd'hui pour pouvoir l'ouvrir tout de suite
    for idx, (h, a, code, when) in enumerate([
        (0, 1, "M-DEMO01", datetime.now() + timedelta(hours=2)),
        (2, 3, "M-DEMO02", datetime.now() + timedelta(days=7)),
    ]):
        mid = uid()
        c.execute("""INSERT INTO matches
            (id, matchday, home_club, away_club, kickoff, stadium, access_code,
             status, referee_id, referee_status)
            VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (mid, 1, club_ids[h], club_ids[a], when.isoformat(),
             clubs[h][3], code, "PRET", ref_id, "CONFIRME"))
        for cid in (club_ids[h], club_ids[a]):
            c.execute("INSERT INTO sheets (id, match_id, club_id) VALUES (?,?,?)",
                      (uid(), mid, cid))


# ══════════════════════════════════════════════════════════════════════
#  ACCÈS AUX RÈGLES ET AU JOURNAL
# ══════════════════════════════════════════════════════════════════════

def rule(c, key, cast=int):
    row = c.execute("SELECT value FROM rules WHERE key=?", (key,)).fetchone()
    if row is None:
        return cast(DEFAULT_RULES[key][0])
    return cast(row["value"])


def log(c, who, action, detail="", reason=""):
    c.execute("INSERT INTO audit (at, who, action, detail, reason) VALUES (?,?,?,?,?)",
              (datetime.now().isoformat(timespec="seconds"), who, action, detail, reason))


# ══════════════════════════════════════════════════════════════════════
#  CONTRÔLE D'ÉLIGIBILITÉ — les six règles, dans l'ordre
#  C'est le cœur du système. La première règle qui échoue arrête
#  l'évaluation et son motif est renvoyé au club.
# ══════════════════════════════════════════════════════════════════════

def check_eligibility(c, player, match):
    """Retourne (True, None) si le joueur peut jouer, sinon (False, motif)."""
    match_day = datetime.fromisoformat(match["kickoff"]).date()

    # 1. Licence délivrée
    if not player["licence_no"]:
        return False, "Aucune licence délivrée"

    # 2. Licence active
    if player["licence_status"] != "ACTIVE":
        return False, f"Licence {player['licence_status'].lower()}"

    # 3. Aucune suspension en cours
    susp = c.execute("""SELECT * FROM suspensions
                        WHERE player_id=? AND status='ACTIVE' AND remaining>0
                        ORDER BY created_at DESC LIMIT 1""",
                     (player["id"],)).fetchone()
    if susp:
        s = "s" if susp["remaining"] > 1 else ""
        return False, (f"Suspendu — {susp['remaining']} match{s} restant{s} "
                       f"— {susp['reason']}")

    # 4. Contrat couvrant la date du match
    if not player["contract_start"] or not player["contract_end"]:
        return False, "Aucun contrat enregistré"
    start = date.fromisoformat(player["contract_start"])
    end = date.fromisoformat(player["contract_end"])
    if not (start <= match_day <= end):
        return False, (f"Contrat hors période "
                       f"({start:%d/%m/%Y} → {end:%d/%m/%Y})")

    # 5. Certificat médical valide
    if not player["medical_expiry"]:
        return False, "Certificat médical absent"
    if date.fromisoformat(player["medical_expiry"]) < match_day:
        expiry = date.fromisoformat(player["medical_expiry"])
        return False, f"Certificat médical expiré le {expiry:%d/%m/%Y}"

    # 6. Pas déjà inscrit ailleurs sur la même journée
    other = c.execute("""SELECT m.id FROM sheet_players sp
                         JOIN sheets s ON sp.sheet_id = s.id
                         JOIN matches m ON s.match_id = m.id
                         WHERE sp.player_id=? AND m.matchday=? AND m.id<>?
                           AND s.status IN ('VALIDEE','VERROUILLEE','TRANSMISE')""",
                      (player["id"], match["matchday"], match["id"])).fetchone()
    if other:
        return False, "Déjà inscrit sur la feuille d'un autre club cette journée"

    return True, None


def validate_composition(c, sheet_id):
    """Contrôles de forme de la feuille. Retourne la liste des anomalies."""
    rows = c.execute("SELECT * FROM sheet_players WHERE sheet_id=?", (sheet_id,)).fetchall()
    errors = []

    mini, maxi = rule(c, "min_players_sheet"), rule(c, "max_players_sheet")
    if len(rows) < mini:
        errors.append(f"{len(rows)} joueurs inscrits, le minimum est de {mini}.")
    if len(rows) > maxi:
        errors.append(f"{len(rows)} joueurs inscrits, le maximum est de {maxi}.")

    starters = [r for r in rows if r["starter"]]
    if len(starters) != 11:
        errors.append(f"Il faut 11 titulaires, {len(starters)} sont désignés.")

    captains = [r for r in rows if r["captain"]]
    if len(captains) != 1:
        errors.append("Désignez exactement un capitaine.")
    elif not captains[0]["starter"]:
        errors.append("Le capitaine doit être titulaire.")

    return errors


# ══════════════════════════════════════════════════════════════════════
#  MOTEUR DISCIPLINAIRE
#  S'exécute à chaque transmission de feuille.
#  Règle intangible : une sanction de source COMMISSION n'est jamais
#  écrasée par le calcul automatique.
# ══════════════════════════════════════════════════════════════════════

def process_discipline(c, match, who):
    """Applique les conséquences disciplinaires. Retourne les suspensions créées."""
    created = []
    cards = c.execute("""SELECT * FROM events WHERE match_id=?
                         AND type IN ('CARTON_JAUNE','CARTON_ROUGE')
                         ORDER BY minute""", (match["id"],)).fetchall()

    by_player = {}
    for card in cards:
        if card["player_id"]:
            by_player.setdefault(card["player_id"], []).append(card)

    for player_id, player_cards in by_player.items():
        reds = [x for x in player_cards if x["type"] == "CARTON_ROUGE"]
        yellows = [x for x in player_cards if x["type"] == "CARTON_JAUNE"]

        if reds:
            red = reds[0]
            if red["card_kind"] == "ROUGE_DIRECT":
                nb = rule(c, "red_direct_suspension_matches")
                motif = red["reason"] or "Carton rouge direct"
            else:
                nb = rule(c, "red_cumulative_suspension_matches")
                motif = "Exclusion pour second avertissement"
            created.append(add_suspension(c, player_id, match["id"], nb, motif, who))
            # Le rouge par cumul consomme les jaunes du match : le compteur de
            # saison n'est pas incrémenté, la sanction couvre déjà le fait.
            continue

        if yellows:
            row = c.execute("SELECT count FROM yellows WHERE player_id=?",
                            (player_id,)).fetchone()
            total = (row["count"] if row else 0) + len(yellows)
            c.execute("INSERT OR REPLACE INTO yellows VALUES (?,?)", (player_id, total))

            threshold = rule(c, "yellow_cards_threshold")
            if total >= threshold:
                nb = rule(c, "yellow_suspension_matches")
                created.append(add_suspension(
                    c, player_id, match["id"], nb,
                    f"Cumul de {threshold} avertissements", who))
                if rule(c, "reset_yellows_after_suspension"):
                    c.execute("UPDATE yellows SET count=0 WHERE player_id=?", (player_id,))

    decrement_suspensions(c, match, who)
    return created


def add_suspension(c, player_id, match_id, nb, reason, who, source="AUTOMATIQUE"):
    sid = uid()
    c.execute("""INSERT INTO suspensions
        (id, player_id, origin_match, total, remaining, reason, source, status, created_at)
        VALUES (?,?,?,?,?,?,?,'ACTIVE',?)""",
        (sid, player_id, match_id, nb, nb, reason, source,
         datetime.now().isoformat(timespec="seconds")))
    name = player_name(c, player_id)
    log(c, who, "SUSPENSION_CREEE", f"{name} — {nb} match(s)", reason)
    return {"id": sid, "player": name, "matches": nb, "reason": reason}


def decrement_suspensions(c, match, who):
    """Décompte les peines en cours des joueurs des deux clubs.

    La peine se purge dès que le club joue un match officiel, que le joueur
    ait été inscrit sur la feuille ou non.
    """
    rows = c.execute("""SELECT s.* FROM suspensions s
                        JOIN players p ON s.player_id = p.id
                        WHERE s.status='ACTIVE' AND s.remaining>0
                          AND p.club_id IN (?,?)
                          AND (s.origin_match IS NULL OR s.origin_match<>?)""",
                     (match["home_club"], match["away_club"], match["id"])).fetchall()

    for row in rows:
        left = row["remaining"] - 1
        status = "PURGEE" if left == 0 else "ACTIVE"
        c.execute("UPDATE suspensions SET remaining=?, status=? WHERE id=?",
                  (left, status, row["id"]))
        if left == 0:
            log(c, who, "SUSPENSION_PURGEE", player_name(c, row["player_id"]))


def player_name(c, player_id):
    row = c.execute("SELECT first_name, last_name FROM players WHERE id=?",
                    (player_id,)).fetchone()
    return f"{row['first_name']} {row['last_name']}" if row else "?"


def discipline_status(c, player_id):
    y = c.execute("SELECT count FROM yellows WHERE player_id=?", (player_id,)).fetchone()
    s = c.execute("""SELECT * FROM suspensions WHERE player_id=? AND status='ACTIVE'
                     ORDER BY created_at DESC LIMIT 1""", (player_id,)).fetchone()
    return (y["count"] if y else 0), s


# ══════════════════════════════════════════════════════════════════════
#  INTERFACE
# ══════════════════════════════════════════════════════════════════════

st.set_page_config(page_title="FOOTCLUBSENEGAL.SN", page_icon="🇸🇳", layout="wide")

st.markdown("""
<style>
  .stApp { background: #F2F1EC; }
  h1, h2, h3 { color: #0A2E1F; }
  .band { height: 5px; display: flex; margin-bottom: 14px; }
  .band i { flex: 1; height: 5px; display: block; }
  .ok    { color:#006B32; background:#E6F2EA; padding:3px 8px; border-radius:3px;
           font-size:12px; font-weight:600; }
  .wait  { color:#8A6A0A; background:#FBF3DC; padding:3px 8px; border-radius:3px;
           font-size:12px; font-weight:600; }
  .bad   { color:#96251E; background:#FBEAE8; padding:3px 8px; border-radius:3px;
           font-size:12px; font-weight:600; }
  .lic   { border:1px solid #C3C0B4; border-radius:8px; background:#fff;
           padding:14px; max-width:340px; }
  .licno { font-family: monospace; font-size:15px; font-weight:700;
           border-top:1px dashed #DCDAD1; padding-top:8px; margin-top:8px; }
</style>
""", unsafe_allow_html=True)


def band():
    st.markdown('<div class="band"><i style="background:#00853F"></i>'
                '<i style="background:#E8C733"></i>'
                '<i style="background:#C4342B"></i></div>', unsafe_allow_html=True)


def tag(text, kind="ok"):
    return f'<span class="{kind}">{text}</span>'


init_db()

if "user" not in st.session_state:
    st.session_state.user = None


# ── Connexion ────────────────────────────────────────────────────────

def login_screen():
    band()
    st.title("FOOTCLUBSENEGAL.SN")
    st.caption("Prototype d'essai — gestion du football professionnel sénégalais")

    col1, col2 = st.columns([1, 1])

    with col1:
        st.subheader("Connexion")
        email = st.text_input("Adresse e-mail")
        password = st.text_input("Mot de passe", type="password")
        if st.button("Se connecter", type="primary"):
            c = conn()
            row = c.execute("SELECT * FROM users WHERE email=? AND password=?",
                            (email.strip().lower(), pw(password))).fetchone()
            if row:
                st.session_state.user = dict(row)
                log(c, row["email"], "CONNEXION")
                c.commit()
                c.close()
                st.rerun()
            else:
                c.close()
                st.error("Adresse ou mot de passe incorrect.")

    with col2:
        st.subheader("Comptes de démonstration")
        st.markdown("""
| Profil | Adresse | Mot de passe |
|---|---|---|
| Ligue | `ligue@lsfp.sn` | `ligue123` |
| Club — Jaraaf | `jar@club.sn` | `club123` |
| Club — Teungueth | `tfc@club.sn` | `club123` |
| Club — Casa Sports | `cas@club.sn` | `club123` |
| Arbitre | `arbitre@fsf.sn` | `arbitre123` |
        """)
        st.info("Jaraaf et Teungueth ont déjà des licences : commencez par eux "
                "pour composer une feuille. Casa Sports et Génération Foot ont "
                "des dossiers incomplets, utiles pour tester le blocage.")


# ── Portail Club ─────────────────────────────────────────────────────

def club_effectif(c, user):
    st.subheader("Effectif et dossiers")
    players = c.execute("SELECT * FROM players WHERE club_id=? ORDER BY shirt_number",
                        (user["club_id"],)).fetchall()

    rows = []
    for p in players:
        docs = [d for d in (p["docs"] or "").split(";") if d]
        missing = [d for d in REQUIRED_DOCS if d not in docs]
        yellow, susp = discipline_status(c, p["id"])
        rows.append({
            "Nº": p["shirt_number"],
            "Joueur": f"{p['first_name']} {p['last_name']}",
            "Poste": p["position"],
            "Dossier": p["dossier_status"],
            "Licence": p["licence_no"] or "—",
            "Pièces manquantes": ", ".join(missing) or "—",
            "Jaunes": yellow,
            "Suspension": f"{susp['remaining']} match(s)" if susp else "—",
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Compléter et soumettre un dossier")

    incomplets = [p for p in players
                  if p["dossier_status"] in ("BROUILLON", "REJETE")]
    if not incomplets:
        st.success("Tous vos dossiers ont été soumis.")
        return

    choix = st.selectbox(
        "Joueur", incomplets,
        format_func=lambda p: f"{p['shirt_number']} — {p['first_name']} {p['last_name']} "
                              f"({p['dossier_status']})")
    docs = [d for d in (choix["docs"] or "").split(";") if d]
    missing = [d for d in REQUIRED_DOCS if d not in docs]

    for d in REQUIRED_DOCS:
        present = d in docs
        col1, col2 = st.columns([3, 1])
        col1.markdown(f"{'✅' if present else '⬜'} {d}")
        if not present and col2.button("Déposer", key=f"doc-{choix['id']}-{d}"):
            docs.append(d)
            c.execute("UPDATE players SET docs=? WHERE id=?",
                      (";".join(docs), choix["id"]))
            log(c, user["email"], "PIECE_DEPOSEE", f"{choix['last_name']} — {d}")
            c.commit()
            st.rerun()

    if missing:
        st.warning(f"{len(missing)} pièce(s) manquante(s). "
                   "Le dossier ne peut pas être soumis.")
    else:
        if st.button("Soumettre le dossier à la Ligue", type="primary"):
            c.execute("UPDATE players SET dossier_status='EN_ATTENTE' WHERE id=?",
                      (choix["id"],))
            log(c, user["email"], "DOSSIER_SOUMIS",
                f"{choix['first_name']} {choix['last_name']}")
            c.commit()
            st.success("Dossier transmis à la Ligue.")
            st.rerun()


def club_feuille(c, user):
    st.subheader("Composition de la feuille de match")

    matches = c.execute("""SELECT * FROM matches WHERE home_club=? OR away_club=?
                           ORDER BY kickoff""",
                        (user["club_id"], user["club_id"])).fetchall()
    if not matches:
        st.info("Aucun match programmé.")
        return

    def label(m):
        home = club_name(c, m["home_club"])
        away = club_name(c, m["away_club"])
        when = datetime.fromisoformat(m["kickoff"])
        return f"J{m['matchday']} · {home} — {away} · {when:%d/%m %H:%M} · {m['status']}"

    match = st.selectbox("Match", matches, format_func=label)
    sheet = c.execute("SELECT * FROM sheets WHERE match_id=? AND club_id=?",
                      (match["id"], user["club_id"])).fetchone()

    st.markdown(f"**Feuille : {sheet['status']}** — code du match "
                f"`{match['access_code']}`")
    if sheet["reason"]:
        st.error(f"Rejetée par la Ligue : {sheet['reason']}")

    if sheet["status"] in ("VERROUILLEE", "TRANSMISE"):
        st.warning("Feuille verrouillée. Toute modification relève de la Ligue.")

    editable = sheet["status"] in ("BROUILLON", "REJETEE")

    players = c.execute("SELECT * FROM players WHERE club_id=? ORDER BY shirt_number",
                        (user["club_id"],)).fetchall()
    current = {r["player_id"]: r for r in c.execute(
        "SELECT * FROM sheet_players WHERE sheet_id=?", (sheet["id"],)).fetchall()}

    eligible, blocked = [], []
    for p in players:
        ok, why = check_eligibility(c, p, match)
        (eligible if ok else blocked).append((p, why))

    st.markdown(f"#### Joueurs sélectionnables ({len(eligible)})")

    selection = {}
    for p, _ in eligible:
        existing = current.get(p["id"])
        default = 0 if existing is None else (1 if existing["starter"] else 2)
        col1, col2, col3 = st.columns([3, 2, 1])
        col1.markdown(f"**{p['shirt_number']}** · {p['first_name']} {p['last_name']} "
                      f"— {p['position']}")
        role = col2.radio("", ["Non retenu", "Titulaire", "Remplaçant"],
                          index=default, key=f"role-{sheet['id']}-{p['id']}",
                          horizontal=True, label_visibility="collapsed",
                          disabled=not editable)
        cap = col3.checkbox("Capitaine", key=f"cap-{sheet['id']}-{p['id']}",
                            value=bool(existing and existing["captain"]),
                            disabled=not editable)
        if role != "Non retenu":
            selection[p["id"]] = (role == "Titulaire", cap)

    if blocked:
        st.markdown(f"#### Joueurs non sélectionnables ({len(blocked)})")
        for p, why in blocked:
            st.markdown(
                f"<div style='padding:8px 12px;background:#FDFAFA;"
                f"border-left:3px solid #C4342B;margin-bottom:5px;border-radius:3px'>"
                f"<b>{p['shirt_number']} · {p['first_name']} {p['last_name']}</b>"
                f"<br><span style='color:#96251E;font-size:13px'>{why}</span></div>",
                unsafe_allow_html=True)

    st.divider()
    titulaires = sum(1 for s, _ in selection.values() if s)
    capitaines = sum(1 for _, cap in selection.values() if cap)

    col1, col2, col3 = st.columns(3)
    col1.metric("Titulaires", f"{titulaires}/11")
    col2.metric("Effectif inscrit", len(selection))
    col3.metric("Capitaine", "défini" if capitaines == 1 else "manquant")

    if editable:
        col1, col2 = st.columns(2)

        if col1.button("Enregistrer la composition"):
            c.execute("DELETE FROM sheet_players WHERE sheet_id=?", (sheet["id"],))
            for pid, (starter, cap) in selection.items():
                c.execute("""INSERT INTO sheet_players
                    (id, sheet_id, player_id, starter, captain) VALUES (?,?,?,?,?)""",
                    (uid(), sheet["id"], pid, int(starter), int(cap)))
            log(c, user["email"], "FEUILLE_COMPOSEE",
                f"{len(selection)} joueurs")
            c.commit()
            st.success("Composition enregistrée.")
            st.rerun()

        if col2.button("Transmettre à la Ligue", type="primary"):
            c.execute("DELETE FROM sheet_players WHERE sheet_id=?", (sheet["id"],))
            for pid, (starter, cap) in selection.items():
                c.execute("""INSERT INTO sheet_players
                    (id, sheet_id, player_id, starter, captain) VALUES (?,?,?,?,?)""",
                    (uid(), sheet["id"], pid, int(starter), int(cap)))
            errors = validate_composition(c, sheet["id"])
            if errors:
                c.rollback()
                for e in errors:
                    st.error(e)
            else:
                c.execute("UPDATE sheets SET status='SOUMISE', reason=NULL WHERE id=?",
                          (sheet["id"],))
                log(c, user["email"], "FEUILLE_SOUMISE", label(match))
                c.commit()
                st.success("Feuille transmise à la Ligue.")
                st.rerun()


def club_discipline(c, user):
    st.subheader("Discipline")
    rows = c.execute("""SELECT s.*, p.first_name, p.last_name FROM suspensions s
                        JOIN players p ON s.player_id = p.id
                        WHERE p.club_id=? ORDER BY s.created_at DESC""",
                     (user["club_id"],)).fetchall()
    if not rows:
        st.success("Aucune suspension. Tous vos joueurs licenciés sont sélectionnables.")
        return

    st.dataframe(pd.DataFrame([{
        "Joueur": f"{r['first_name']} {r['last_name']}",
        "Motif": r["reason"],
        "Restant": f"{r['remaining']}/{r['total']}",
        "Source": r["source"],
        "Statut": r["status"],
        "Prononcée": r["created_at"][:10],
    } for r in rows]), use_container_width=True, hide_index=True)


def club_reclamation(c, user):
    st.subheader("Réclamations")
    joues = c.execute("""SELECT * FROM matches WHERE status IN ('TERMINE','HOMOLOGUE')
                         AND (home_club=? OR away_club=?)""",
                      (user["club_id"], user["club_id"])).fetchall()

    if joues:
        with st.form("claim"):
            match = st.selectbox(
                "Match", joues,
                format_func=lambda m: f"{club_name(c, m['home_club'])} "
                                      f"{m['home_score']}–{m['away_score']} "
                                      f"{club_name(c, m['away_club'])}")
            ctype = st.selectbox("Objet", CLAIM_TYPES)
            body = st.text_area("Exposé des faits",
                                placeholder="Soyez précis : la commission statue sur ce texte.")
            if st.form_submit_button("Déposer la réclamation", type="primary"):
                hours = rule(c, "claim_deadline_hours")
                deadline = datetime.fromisoformat(match["kickoff"]) + timedelta(hours=2 + hours)
                if datetime.now() > deadline:
                    st.error(f"Délai expiré le {deadline:%d/%m/%Y à %H:%M}. "
                             "La réclamation est irrecevable.")
                elif len(body) < 10:
                    st.error("L'exposé des faits est trop court.")
                else:
                    c.execute("""INSERT INTO claims
                        (id, match_id, club_id, type, body, status, filed_at, deadline)
                        VALUES (?,?,?,?,?,'DEPOSEE',?,?)""",
                        (uid(), match["id"], user["club_id"], ctype, body,
                         datetime.now().isoformat(timespec="seconds"),
                         deadline.isoformat(timespec="seconds")))
                    log(c, user["email"], "RECLAMATION_DEPOSEE", ctype)
                    c.commit()
                    st.success("Réclamation déposée. La commission va l'instruire.")
                    st.rerun()
    else:
        st.info("Aucun match terminé. Vous pourrez déposer une réclamation après un match.")

    mine = c.execute("SELECT * FROM claims WHERE club_id=? ORDER BY filed_at DESC",
                     (user["club_id"],)).fetchall()
    for r in mine:
        st.markdown(f"**{r['type']}** — {r['status']}")
        st.caption(r["body"])
        if r["reasoning"]:
            st.info(f"Décision : {r['decision']} — {r['reasoning']}")
        st.divider()


def club_paie(c, user):
    st.subheader("Rémunérations")
    st.caption("Salaires et primes calculés sur les feuilles de match transmises. "
               "Ces données ne sont visibles que de votre club.")

    players = c.execute("SELECT * FROM players WHERE club_id=? ORDER BY shirt_number",
                        (user["club_id"],)).fetchall()
    rows = []
    for p in players:
        played = c.execute("""SELECT COUNT(*) n FROM sheet_players sp
                              JOIN sheets s ON sp.sheet_id=s.id
                              WHERE sp.player_id=? AND s.status='TRANSMISE'""",
                           (p["id"],)).fetchone()["n"]
        goals = c.execute("""SELECT COUNT(*) n FROM events
                             WHERE player_id=? AND type='BUT' AND own_goal=0""",
                          (p["id"],)).fetchone()["n"]
        total = p["salary"] + played * p["match_bonus"] + goals * p["goal_bonus"]
        rows.append({
            "Nº": p["shirt_number"],
            "Joueur": f"{p['first_name']} {p['last_name']}",
            "Salaire": f"{p['salary']:,}".replace(",", " "),
            "Matchs": played,
            "Buts": goals,
            "Total (FCFA)": f"{total:,}".replace(",", " "),
        })
    df = pd.DataFrame(rows)
    st.dataframe(df, use_container_width=True, hide_index=True)
    st.download_button("Exporter en CSV", df.to_csv(index=False).encode("utf-8"),
                       "paie.csv", "text/csv")


# ── Portail Ligue ────────────────────────────────────────────────────

def ligue_homologations(c, user):
    st.subheader("Homologations")
    st.caption("La validation délivre immédiatement la licence.")

    rows = c.execute("""SELECT p.*, cl.name club FROM players p
                        JOIN clubs cl ON p.club_id = cl.id
                        WHERE p.dossier_status='EN_ATTENTE'
                        ORDER BY cl.name, p.shirt_number""").fetchall()
    if not rows:
        st.success("Aucun dossier en attente d'instruction.")
    else:
        st.warning(f"{len(rows)} dossier(s) à instruire.")

    for p in rows:
        with st.container(border=True):
            col1, col2 = st.columns([3, 2])
            col1.markdown(f"**{p['first_name']} {p['last_name']}** · {p['club']}")
            col1.caption(f"{p['position']} · né le {p['birth_date']} · "
                         f"nº {p['shirt_number']}")
            docs = [d for d in (p["docs"] or "").split(";") if d]
            col1.caption("Pièces : " + ", ".join(docs))

            decision = col2.radio("Décision", ["Valider", "Complément", "Rejeter"],
                                  key=f"dec-{p['id']}", horizontal=True)
            motif = col2.text_input("Motif", key=f"mot-{p['id']}",
                                    placeholder="Obligatoire si refus")

            if col2.button("Enregistrer", key=f"btn-{p['id']}", type="primary"):
                if decision != "Valider" and not motif:
                    st.error("Un motif est obligatoire pour cette décision.")
                elif decision == "Valider":
                    n = c.execute("SELECT COUNT(*) n FROM players WHERE licence_no IS NOT NULL"
                                  ).fetchone()["n"]
                    numero = f"SN-2027-L1-{n + 1:06d}"
                    c.execute("""UPDATE players SET dossier_status='VALIDE',
                                 licence_no=?, licence_status='ACTIVE' WHERE id=?""",
                              (numero, p["id"]))
                    log(c, user["email"], "HOMOLOGATION_VALIDEE",
                        f"{p['first_name']} {p['last_name']} — {numero}")
                    c.commit()
                    st.success(f"Licence {numero} délivrée.")
                    st.rerun()
                else:
                    status = "REJETE" if decision == "Rejeter" else "BROUILLON"
                    c.execute("UPDATE players SET dossier_status=?, dossier_reason=? WHERE id=?",
                              (status, motif, p["id"]))
                    log(c, user["email"], f"HOMOLOGATION_{decision.upper()}",
                        f"{p['first_name']} {p['last_name']}", motif)
                    c.commit()
                    st.rerun()


def ligue_feuilles(c, user):
    st.subheader("Feuilles de match")

    rows = c.execute("""SELECT s.*, cl.name club, m.access_code, m.kickoff,
                               m.home_club, m.away_club, m.matchday
                        FROM sheets s
                        JOIN clubs cl ON s.club_id = cl.id
                        JOIN matches m ON s.match_id = m.id
                        ORDER BY m.kickoff, cl.name""").fetchall()

    for s in rows:
        with st.container(border=True):
            n = c.execute("SELECT COUNT(*) n FROM sheet_players WHERE sheet_id=?",
                          (s["id"],)).fetchone()["n"]
            col1, col2 = st.columns([3, 2])
            col1.markdown(f"**{s['club']}** — {s['status']}")
            col1.caption(f"J{s['matchday']} · {club_name(c, s['home_club'])} contre "
                         f"{club_name(c, s['away_club'])} · code `{s['access_code']}` · "
                         f"{n} joueur(s)")

            if s["status"] == "SOUMISE":
                if col2.button("Valider", key=f"v-{s['id']}", type="primary"):
                    c.execute("UPDATE sheets SET status='VALIDEE' WHERE id=?", (s["id"],))
                    log(c, user["email"], "FEUILLE_VALIDEE", s["club"])
                    c.commit()
                    st.rerun()
                motif = col2.text_input("Motif de rejet", key=f"r-{s['id']}")
                if col2.button("Rejeter", key=f"rj-{s['id']}"):
                    if not motif:
                        st.error("Indiquez le motif du rejet.")
                    else:
                        c.execute("UPDATE sheets SET status='REJETEE', reason=? WHERE id=?",
                                  (motif, s["id"]))
                        log(c, user["email"], "FEUILLE_REJETEE", s["club"], motif)
                        c.commit()
                        st.rerun()

            elif s["status"] == "VALIDEE":
                if col2.button("Verrouiller (T-30 min)", key=f"l-{s['id']}"):
                    c.execute("UPDATE sheets SET status='VERROUILLEE' WHERE id=?",
                              (s["id"],))
                    log(c, user["email"], "FEUILLE_VERROUILLEE", s["club"])
                    c.commit()
                    st.rerun()


def ligue_discipline(c, user):
    st.subheader("Discipline")
    st.caption("Une révision par la commission fige la sanction : "
               "le moteur automatique ne la recalcule plus.")

    rows = c.execute("""SELECT s.*, p.first_name, p.last_name, cl.name club
                        FROM suspensions s
                        JOIN players p ON s.player_id=p.id
                        JOIN clubs cl ON p.club_id=cl.id
                        ORDER BY s.created_at DESC""").fetchall()
    if not rows:
        st.info("Aucune suspension enregistrée.")
        return

    for s in rows:
        with st.container(border=True):
            col1, col2 = st.columns([3, 2])
            col1.markdown(f"**{s['first_name']} {s['last_name']}** · {s['club']}")
            col1.caption(f"{s['reason']} — {s['remaining']}/{s['total']} match(s) "
                         f"restant(s) · source {s['source']} · {s['status']}")

            if s["status"] == "ACTIVE":
                new = col2.number_input("Matchs restants", 0, 10, s["remaining"],
                                        key=f"n-{s['id']}")
                motif = col2.text_input("Motif de la révision", key=f"m-{s['id']}")
                if col2.button("Réviser", key=f"rev-{s['id']}"):
                    if len(motif) < 5:
                        st.error("Le motif est obligatoire.")
                    else:
                        status = "PURGEE" if new == 0 else "ACTIVE"
                        c.execute("""UPDATE suspensions SET remaining=?, status=?,
                                     source='COMMISSION', reason=? WHERE id=?""",
                                  (new, status, f"{s['reason']} — révisé : {motif}",
                                   s["id"]))
                        log(c, user["email"], "SUSPENSION_REVISEE",
                            f"{s['first_name']} {s['last_name']}", motif)
                        c.commit()
                        st.success("Sanction révisée.")
                        st.rerun()


def ligue_commission(c, user):
    st.subheader("Commission de discipline")
    rows = c.execute("""SELECT cm.*, cl.name club FROM claims cm
                        JOIN clubs cl ON cm.club_id=cl.id
                        ORDER BY cm.filed_at DESC""").fetchall()
    if not rows:
        st.info("Aucune réclamation déposée.")
        return

    for r in rows:
        with st.container(border=True):
            st.markdown(f"**{r['type']}** — déposée par {r['club']} — {r['status']}")
            st.caption(r["body"])
            if r["status"] == "DEPOSEE":
                decision = st.radio("Décision", ["Accepter", "Rejeter"],
                                    key=f"cd-{r['id']}", horizontal=True)
                motivation = st.text_area("Motivation", key=f"cm-{r['id']}")
                if st.button("Rendre la décision", key=f"cb-{r['id']}", type="primary"):
                    if len(motivation) < 10:
                        st.error("La motivation est obligatoire.")
                    else:
                        status = "ACCEPTEE" if decision == "Accepter" else "REJETEE"
                        c.execute("""UPDATE claims SET status=?, decision=?, reasoning=?
                                     WHERE id=?""", (status, status, motivation, r["id"]))
                        log(c, user["email"], "COMMISSION_DECISION", r["type"], motivation)
                        c.commit()
                        st.success("Décision enregistrée et notifiée aux deux clubs.")
                        st.rerun()
            elif r["reasoning"]:
                st.info(f"{r['decision']} — {r['reasoning']}")


def ligue_regles(c, user):
    st.subheader("Règles de la compétition")
    st.warning("Ces valeurs sont des hypothèses de travail. Elles doivent être "
               "confirmées par la LSFP avant toute mise en service.")

    rows = c.execute("SELECT * FROM rules ORDER BY key").fetchall()
    for r in rows:
        col1, col2, col3 = st.columns([3, 1, 1])
        col1.markdown(f"**{r['label']}**")
        col1.caption(f"`{r['key']}`")
        val = col2.text_input("", r["value"], key=f"rule-{r['key']}",
                              label_visibility="collapsed")
        if col3.button("Enregistrer", key=f"rb-{r['key']}"):
            c.execute("UPDATE rules SET value=? WHERE key=?", (val, r["key"]))
            log(c, user["email"], "REGLE_MODIFIEE",
                f"{r['key']} : {r['value']} → {val}")
            c.commit()
            st.success("Règle appliquée immédiatement.")
            st.rerun()


def ligue_audit(c, user):
    st.subheader("Journal d'audit")
    st.caption("Toute action officielle est enregistrée. Dans la plateforme réelle, "
               "la base interdit techniquement la modification et la suppression.")
    rows = c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 200").fetchall()
    st.dataframe(pd.DataFrame([{
        "Horodatage": r["at"], "Auteur": r["who"], "Action": r["action"],
        "Détail": r["detail"], "Motif": r["reason"] or "",
    } for r in rows]), use_container_width=True, hide_index=True)


# ── Espace Arbitre ───────────────────────────────────────────────────

def arbitre(c, user):
    st.subheader("Feuille de match")

    if "match_ouvert" not in st.session_state:
        st.session_state.match_ouvert = None

    mine = c.execute("SELECT * FROM matches WHERE referee_id=? ORDER BY kickoff",
                     (user["id"],)).fetchall()
    st.markdown("#### Mes désignations")
    for m in mine:
        when = datetime.fromisoformat(m["kickoff"])
        st.markdown(f"- **{club_name(c, m['home_club'])} — {club_name(c, m['away_club'])}** "
                    f"· {when:%d/%m à %H:%M} · code `{m['access_code']}` · {m['status']}")

    code = st.text_input("Code d'accès du match", placeholder="M-DEMO01")
    if st.button("Ouvrir la feuille", type="primary"):
        m = c.execute("SELECT * FROM matches WHERE access_code=?",
                      (code.strip().upper(),)).fetchone()
        if not m:
            st.error("Code inconnu.")
        elif m["referee_id"] != user["id"]:
            st.error("Vous n'êtes pas désigné sur ce match.")
        elif m["status"] == "TERMINE":
            st.error("Cette feuille a déjà été transmise.")
        else:
            st.session_state.match_ouvert = m["id"]
            log(c, user["email"], "MATCH_OUVERT", m["access_code"])
            c.commit()
            st.rerun()

    if not st.session_state.match_ouvert:
        return

    match = c.execute("SELECT * FROM matches WHERE id=?",
                      (st.session_state.match_ouvert,)).fetchone()
    st.divider()
    st.markdown(f"### {club_name(c, match['home_club'])} — "
                f"{club_name(c, match['away_club'])}")

    sheets = c.execute("SELECT * FROM sheets WHERE match_id=?", (match["id"],)).fetchall()
    non_pretes = [s for s in sheets
                  if s["status"] not in ("VERROUILLEE", "TRANSMISE", "VALIDEE")]
    if non_pretes:
        st.error("Les deux feuilles doivent être validées par la Ligue avant "
                 "l'ouverture du match.")
        return

    lineups = {}
    for s in sheets:
        lineups[s["club_id"]] = c.execute("""SELECT sp.*, p.first_name, p.last_name,
                                                    p.shirt_number, p.birth_date
                                             FROM sheet_players sp
                                             JOIN players p ON sp.player_id=p.id
                                             WHERE sp.sheet_id=? ORDER BY p.shirt_number""",
                                          (s["id"],)).fetchall()

    onglet1, onglet2, onglet3 = st.tabs(
        ["Contrôle d'identité", "Faits de jeu", "Signatures et transmission"])

    # ── Contrôle d'identité
    with onglet1:
        for club_id, lines in lineups.items():
            st.markdown(f"**{club_name(c, club_id)}**")
            for l in lines:
                col1, col2 = st.columns([4, 1])
                col1.markdown(f"{l['shirt_number']} · {l['first_name']} {l['last_name']} "
                              f"— né le {l['birth_date']}"
                              f"{' · capitaine' if l['captain'] else ''}")
                checked = col2.checkbox("Identifié", value=bool(l["checked"]),
                                        key=f"id-{l['id']}")
                if checked != bool(l["checked"]):
                    c.execute("UPDATE sheet_players SET checked=? WHERE id=?",
                              (int(checked), l["id"]))
                    c.commit()

    # ── Faits de jeu
    with onglet2:
        events = c.execute("SELECT * FROM events WHERE match_id=? ORDER BY minute",
                           (match["id"],)).fetchall()
        exclus = {e["player_id"] for e in events if e["type"] == "CARTON_ROUGE"}

        with st.form("event"):
            col1, col2 = st.columns(2)
            type_fait = col1.selectbox(
                "Type", ["But", "Carton jaune", "Carton rouge", "Remplacement"])
            minute = col2.number_input("Minute", 0, 120, 1)

            club_choisi = st.selectbox(
                "Équipe", [match["home_club"], match["away_club"]],
                format_func=lambda cid: club_name(c, cid))

            dispo = [l for l in lineups[club_choisi] if l["player_id"] not in exclus]
            if not dispo:
                st.warning("Aucun joueur disponible pour cette équipe.")
                joueur = None
            else:
                joueur = st.selectbox(
                    "Joueur", dispo,
                    format_func=lambda l: f"{l['shirt_number']} — {l['first_name']} "
                                          f"{l['last_name']}")

            motif = None
            penalty = own_goal = False
            if type_fait == "Carton jaune":
                motif = st.selectbox("Motif", YELLOW_REASONS)
            elif type_fait == "Carton rouge":
                motif = st.selectbox("Motif", RED_REASONS)
            elif type_fait == "But":
                col1, col2 = st.columns(2)
                penalty = col1.checkbox("Sur penalty")
                own_goal = col2.checkbox("Contre son camp")

            if st.form_submit_button("Enregistrer le fait", type="primary") and joueur:
                etype = {"But": "BUT", "Carton jaune": "CARTON_JAUNE",
                         "Carton rouge": "CARTON_ROUGE",
                         "Remplacement": "REMPLACEMENT"}[type_fait]
                card_kind = None

                # Un second avertissement devient une exclusion par cumul
                if etype == "CARTON_JAUNE":
                    deja = c.execute("""SELECT COUNT(*) n FROM events WHERE match_id=?
                                        AND player_id=? AND type='CARTON_JAUNE'""",
                                     (match["id"], joueur["player_id"])).fetchone()["n"]
                    if deja >= 1:
                        etype, card_kind = "CARTON_ROUGE", "ROUGE_CUMUL"
                        motif = "Second avertissement"
                        st.info("Second avertissement : converti en exclusion par cumul.")
                    else:
                        card_kind = "JAUNE"
                elif etype == "CARTON_ROUGE":
                    card_kind = "ROUGE_DIRECT"

                c.execute("""INSERT INTO events
                    (id, match_id, club_id, player_id, type, minute, card_kind,
                     reason, own_goal, penalty)
                    VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (uid(), match["id"], club_choisi, joueur["player_id"], etype,
                     minute, card_kind, motif, int(own_goal), int(penalty)))
                log(c, user["email"], f"FAIT_{etype}",
                    f"{joueur['first_name']} {joueur['last_name']} — {minute}'")
                c.commit()
                st.rerun()

        if events:
            st.markdown("#### Faits enregistrés")
            for e in events:
                icone = {"BUT": "⚽", "CARTON_JAUNE": "🟨",
                         "CARTON_ROUGE": "🟥", "REMPLACEMENT": "🔄"}[e["type"]]
                extra = []
                if e["penalty"]:
                    extra.append("penalty")
                if e["own_goal"]:
                    extra.append("contre son camp")
                if e["reason"]:
                    extra.append(e["reason"])
                col1, col2 = st.columns([5, 1])
                col1.markdown(f"{icone} **{e['minute']}'** "
                              f"{player_name(c, e['player_id'])} "
                              f"— {club_name(c, e['club_id'])}"
                              + (f" · {', '.join(extra)}" if extra else ""))
                if col2.button("Retirer", key=f"del-{e['id']}"):
                    c.execute("DELETE FROM events WHERE id=?", (e["id"],))
                    log(c, user["email"], "FAIT_SUPPRIME", f"{e['minute']}'")
                    c.commit()
                    st.rerun()

    # ── Signatures et transmission
    with onglet3:
        roles = ["Arbitre central", "Capitaine domicile", "Capitaine extérieur"]
        signatures = {s["role"]: s["signer"] for s in c.execute(
            "SELECT * FROM signatures WHERE match_id=?", (match["id"],)).fetchall()}

        for role in roles:
            if role in signatures:
                st.success(f"{role} — signé par {signatures[role]}")
            else:
                col1, col2 = st.columns([3, 1])
                nom = col1.text_input(role, key=f"sig-{role}",
                                      placeholder="Nom et prénom du signataire")
                if col2.button("Signer", key=f"sb-{role}") and len(nom) > 2:
                    c.execute("INSERT INTO signatures VALUES (?,?,?,?,?)",
                              (uid(), match["id"], role, nom,
                               datetime.now().isoformat(timespec="seconds")))
                    log(c, user["email"], "SIGNATURE", f"{role} : {nom}")
                    c.commit()
                    st.rerun()

        st.divider()
        complet = all(r in signatures for r in roles)
        if not complet:
            st.warning("Les trois signatures sont exigées avant transmission.")

        if st.button("Transmettre la feuille", type="primary", disabled=not complet):
            events = c.execute("SELECT * FROM events WHERE match_id=? AND type='BUT'",
                               (match["id"],)).fetchall()
            home = sum(1 for e in events
                       if (e["club_id"] == match["home_club"] and not e["own_goal"])
                       or (e["club_id"] == match["away_club"] and e["own_goal"]))
            away = len(events) - home

            c.execute("UPDATE matches SET status='TERMINE', home_score=?, away_score=? "
                      "WHERE id=?", (home, away, match["id"]))
            c.execute("UPDATE sheets SET status='TRANSMISE' WHERE match_id=?",
                      (match["id"],))

            suspensions = process_discipline(c, match, user["email"])
            log(c, user["email"], "FEUILLE_TRANSMISE", f"Score {home}-{away}")
            c.commit()

            st.success(f"Feuille transmise. Score final : {home} – {away}")
            if suspensions:
                st.error("**Suspensions prononcées automatiquement :**")
                for s in suspensions:
                    st.markdown(f"- **{s['player']}** — {s['matches']} match(s) "
                                f"— {s['reason']}")
                st.info("Ces joueurs seront automatiquement bloqués à la composition "
                        "du prochain match. Vérifiez-le en vous connectant comme club.")
            st.session_state.match_ouvert = None


def club_name(c, club_id):
    row = c.execute("SELECT name FROM clubs WHERE id=?", (club_id,)).fetchone()
    return row["name"] if row else "?"


# ── Routage principal ────────────────────────────────────────────────

def main():
    if not st.session_state.user:
        login_screen()
        return

    user = st.session_state.user
    c = conn()

    with st.sidebar:
        band()
        st.markdown("### FOOTCLUBSENEGAL.SN")
        st.caption("Prototype d'essai")
        st.markdown(f"**{user['full_name']}**")
        st.caption(f"{user['email']} · {user['role']}")
        st.divider()

        if user["role"] == "CLUB":
            pages = {
                "Effectif et dossiers": club_effectif,
                "Feuille de match": club_feuille,
                "Discipline": club_discipline,
                "Réclamations": club_reclamation,
                "Rémunérations": club_paie,
            }
        elif user["role"] == "LIGUE":
            pages = {
                "Homologations": ligue_homologations,
                "Feuilles de match": ligue_feuilles,
                "Discipline": ligue_discipline,
                "Commission": ligue_commission,
                "Règles de la compétition": ligue_regles,
                "Journal d'audit": ligue_audit,
            }
        else:
            pages = {"Feuille de match": arbitre}

        choix = st.radio("Navigation", list(pages), label_visibility="collapsed")
        st.divider()

        if st.button("Se déconnecter"):
            st.session_state.user = None
            st.session_state.pop("match_ouvert", None)
            st.rerun()

        with st.expander("Réinitialiser la démonstration"):
            st.caption("Efface toutes les données et recrée le jeu d'essai.")
            if st.button("Tout réinitialiser"):
                c.close()
                import os
                if os.path.exists(DB):
                    os.remove(DB)
                st.session_state.user = None
                st.rerun()

    band()
    pages[choix](c, user)
    c.commit()
    c.close()


# Streamlit exécute le script sous le nom __main__ : cette garde ne change donc
# rien au lancement normal, mais permet d'importer le fichier pour tester la
# logique métier sans démarrer l'interface.
if __name__ == "__main__":
    main()
