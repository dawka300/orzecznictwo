"""Polish legal phrases -> their English and French wording in ECHR texts.

The texts are in English and French (Polish translations are ~1% of them),
so a Polish query finds next to nothing. Known phrases are replaced by a
group of alternatives: "prawo do sądu" -> ("right to a court" OR "access to a
court" OR "droit à un tribunal" ...).

Patterns are lowercase without Polish diacritics; "stem*" matches inflected
forms (praw* = prawo, prawa, prawem ...; up to MAX_ENDING extra letters,
SHORT_ENDING after a stem of SHORT_STEM letters or less, so that sad* takes
sądu, sądowego but not sadzonki), a word without * must match exactly.
Longer patterns win over shorter ones.
"""

from __future__ import annotations

import unicodedata

MAX_ENDING = 8  # aresztowania = areszt + 6
SHORT_STEM = 4
SHORT_ENDING = 4

_ENTRIES = """
# art. 2-4
praw* do zyci* = right to life | droit à la vie
kar* smierc* = death penalty | peine de mort
uzyci* sil* = use of force | lethal force | recours à la force
skuteczn* sledztw* = effective investigation | enquête effective
sledztw* = investigation | enquête
tortur* = torture
nieludzk* = inhuman | inhumain
ponizaj* = degrading | dégradant
zl* traktowan* = ill-treatment | mauvais traitements
warunk* osadzeni* = conditions of detention | conditions de détention
warunk* w wiezien* = conditions of detention | conditions de détention
warunk* w aresz* = conditions of detention | conditions de détention
przeludn* = overcrowding | surpeuplement
opiek* medyczn* = medical care | medical assistance | soins médicaux
psychiczn* = mental health | mental disorder | psychiatric | psychiatrique
przemoc* domow* = domestic violence | violence domestique
niewolnic* = slavery | servitude | esclavage
prac* przymusow* = forced labour | travail forcé
handel ludz* = trafficking | traite des êtres humains
handl* ludz* = trafficking | traite des êtres humains
# art. 5
praw* do wolnosc* = right to liberty | droit à la liberté
pozbawien* wolnosc* = deprivation of liberty | privation de liberté
tymczasow* areszt* = pre-trial detention | detention on remand | détention provisoire
areszt* tymczasow* = pre-trial detention | detention on remand | détention provisoire
zatrzyman* = arrest | detention | garde à vue
areszt* = detention | arrest | détention
dozywoc* = life imprisonment | life sentence | réclusion à perpétuité
# art. 6
praw* do sad* = right to a court | access to a court | droit à un tribunal | accès à un tribunal
dostep* do sad* = access to a court | accès à un tribunal
rzeteln* proces* = fair trial | fair hearing | procès équitable
przewlek* = length of proceedings | reasonable time | durée de la procédure | délai raisonnable
domniema* niewinnos* = presumption of innocence | présomption d'innocence
bezstronn* = impartiality | impartial | impartialité
niezawis* = independence | independent tribunal | indépendance
niezalezn* sad* = independent tribunal | independence of the judiciary | tribunal indépendant
sad* ustanowion* = tribunal established by law | tribunal établi par la loi
krajow* rad* sadownictw* = National Council of the Judiciary | Conseil national de la magistrature
krs = National Council of the Judiciary | Conseil national de la magistrature
izb* dyscyplinarn* = Disciplinary Chamber | chambre disciplinaire
izb* kontrol* nadzwyczajn* = Extraordinary Review | contrôle extraordinaire
trybunal* konstytucyjn* = Constitutional Court | Cour constitutionnelle
sad* najwyzsz* = Supreme Court | Cour suprême
rownos* bron* = equality of arms | égalité des armes
jawnos* = public hearing | audience publique
rozpraw* publiczn* = public hearing | audience publique
praw* do obron* = rights of the defence | legal assistance | droits de la défense
pomoc* prawn* = legal assistance | legal aid | assistance juridique
obronc* = lawyer | counsel | legal assistance | avocat
adwokat* = lawyer | counsel | avocat
prawnik* = lawyer | avocat
niewykonan* = non-enforcement | inexécution
wykonan* wyrok* = enforcement | non-enforcement | exécution
sad* = court | tribunal
sedzi* = judge | juge
sedziow* = judges | juges
prokurat* = prosecutor | prosecution | procureur | parquet
# art. 7-14
bez podstaw* prawn* = no punishment without law | pas de peine sans loi
zyci* prywatn* = private life | vie privée
prywatnos* = private life | privacy | vie privée
zyci* rodzinn* = family life | vie familiale
poszanowan* mieszkan* = respect for his home | respect for her home | domicile
korespondencj* = correspondence | correspondance
podsluch* = telephone tapping | interception | écoutes téléphoniques
inwigilac* = secret surveillance | surveillance
dan* osobow* = personal data | données à caractère personnel | données personnelles
wolnosc* myśl* = freedom of thought | liberté de pensée
wolnosc* sumien* = freedom of conscience | liberté de conscience
wolnosc* religi* = freedom of religion | liberté de religion
wolnosc* wyznan* = freedom of religion | liberté de religion
wolnosc* slow* = freedom of expression | liberté d'expression
wolnosc* wypowiedz* = freedom of expression | liberté d'expression
wolnosc* pras* = freedom of the press | liberté de la presse
zniesławien* = defamation | diffamation
znieslawien* = defamation | diffamation
pomowien* = defamation | diffamation
dziennikar* = journalist | journaliste
mow* nienawis* = hate speech | incitement to hatred | discours de haine
wolnosc* zgromadz* = freedom of assembly | freedom of peaceful assembly | liberté de réunion
zgromadzen* = assembly | demonstration | manifestation
wolnosc* zrzesz* = freedom of association | liberté d'association
zwiazk* zawodow* = trade union | syndicat
praw* do zawarci* malzenst* = right to marry | droit au mariage
malzenst* = marriage | mariage
rozwod* = divorce
skuteczn* srod* = effective remedy | recours effectif
dyskrymin* = discrimination
# protokoły
ochron* wlasnos* = protection of property | peaceful enjoyment of possessions | protection de la propriété
praw* wlasnos* = protection of property | peaceful enjoyment of possessions | protection de la propriété
wlasnos* = property | possessions | propriété | biens
wywlaszcz* = expropriation
reprywatyz* = restitution
zwrot* mieni* = restitution
najem = tenancy | rent | loyer
najm* = tenancy | rent | loyer
czynsz* = rent | loyer
podatk* = tax | taxation | impôt
podatek = tax | taxation | impôt
emerytur* = pension | retraite
ubezpiecz* spoleczn* = social security | social insurance | sécurité sociale
praw* do nauk* = right to education | droit à l'instruction
edukac* = education | instruction
woln* wybor* = free elections | élections libres
praw* wyborcz* = right to vote | electoral rights | droit de vote
swobod* przemieszcz* = freedom of movement | liberté de circulation
podwojn* karan* = ne bis in idem | non bis in idem | tried or punished twice
# procedura przed ETPC
wyczerpan* = exhaustion of domestic remedies | épuisement des voies de recours internes
oczywisc* bezzasad* = manifestly ill-founded | manifestement mal fondée
bezzasad* = manifestly ill-founded | manifestement mal fondée
niedopuszcz* = inadmissible | irrecevable
dopuszczaln* = admissibility | recevabilité
status* ofiar* = victim status | qualité de victime
pilotaz* = pilot judgment | arrêt pilote
ugod* = friendly settlement | règlement amiable
jednostronn* deklarac* = unilateral declaration | déclaration unilatérale
skreslen* = strike out | struck out | radiation
sluszn* zadoscuczyn* = just satisfaction | satisfaction équitable
zadoscuczyn* = just satisfaction | non-pecuniary damage | satisfaction équitable | dommage moral
szkod* majatkow* = pecuniary damage | dommage matériel
szkod* niemajatkow* = non-pecuniary damage | dommage moral
naruszen* = violation
# inne
ekstradyc* = extradition
deportac* = expulsion | deportation
wydalen* = expulsion | deportation
azyl* = asylum | asile
uchodzc* = refugee | asylum seeker | réfugié
cudzoziem* = alien | foreigner | étranger
dziec* = child | children | enfant
dzieck* = child | enfant
wladz* rodzicielsk* = parental authority | custody | autorité parentale
kontakt* z dzieck* = contact rights | access rights | droit de visite
uprowadzen* = abduction | enlèvement
tej sam* plc* = same-sex | même sexe
orientac* seksualn* = sexual orientation | orientation sexuelle
tozsamos* plciow* = gender identity | identité de genre
transplciow* = transgender | transsexual | transsexuel
romow* = Roma | Rom | Gypsy
romsk* = Roma | Rom | Gypsy
aborc* = abortion | avortement
przerwan* ciaz* = abortion | avortement
eutanaz* = euthanasia | assisted suicide | euthanasie
srodowisk* = environment | environmental | environnement | pollution
policj* = police
wiezien* = prison | prisoner | détenu
wiezn* = prisoner | detainee | détenu
zaklad* karn* = prison | établissement pénitentiaire
lustrac* = lustration
zwolnien* z prac* = dismissal | licenciement
zatrudn* = employment | emploi
polsk* = Poland | Pologne
polsc* = Poland | Pologne
"""

# Spójniki i przyimki opuszczane w zapytaniu tłumaczonym.
_POLISH_STOPWORDS = (
    "a albo bez co czy dla do i jak jest lub na nad nie o od oraz po pod przez przeciwko"
    " przy sa sie ta tej ten to u w we z za ze"
)
POLISH_STOPWORDS = frozenset(_POLISH_STOPWORDS.split())


def normalize(word):
    """Lowercase without Polish diacritics: Sądu -> sadu, ŁÓDŹ -> lodz."""
    word = word.lower().replace("ł", "l")
    return "".join(c for c in unicodedata.normalize("NFKD", word) if not unicodedata.combining(c))


def _parse(text):
    entries = []
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        pattern, alternatives = line.split("=", 1)
        words = tuple(normalize(w) for w in pattern.split())
        entries.append((words, [a.strip() for a in alternatives.split("|")]))
    # dłuższe wzorce najpierw: "praw* do sad*" przed "sad*"
    entries.sort(key=lambda e: -len(e[0]))
    return entries


ENTRIES = _parse(_ENTRIES)


def _matches(pattern, word):
    if pattern.endswith("*"):
        stem = pattern[:-1]
        ending = SHORT_ENDING if len(stem) <= SHORT_STEM else MAX_ENDING
        return word.startswith(stem) and len(word) - len(stem) <= ending
    return word == pattern


def translate(words):
    """Split a word list into [(original words, alternatives or None)]."""
    norm = [normalize(w) for w in words]
    out, i = [], 0
    while i < len(words):
        for pattern, alternatives in ENTRIES:
            n = len(pattern)
            if i + n <= len(words) and all(_matches(p, w) for p, w in zip(pattern, norm[i:])):
                out.append((words[i : i + n], alternatives))
                i += n
                break
        else:
            out.append(([words[i]], None))
            i += 1
    return out
