import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { estVide, fmtDate, fmtEntier, fmtMontant, fmtPct } from '../format.js';
import { LIBELLES_STATUT } from '../datasets.js';
import StatusBadge from './StatusBadge.jsx';

const TAILLE_PAGE = 50;
const NUMERIQUES = new Set(['montant', 'entier', 'pct', 'variation', 'atteinte']);
// Nombre maximal de valeurs listées dans le menu d'un filtre : au-delà, la
// recherche du menu sert à retrouver une valeur.
const MAX_VALEURS = 500;
const LARGEUR_MENU = 280;

function afficher(col, ligne) {
  const v = ligne[col.cle];
  switch (col.type) {
    case 'montant':
      return fmtMontant(v);
    case 'entier':
      return fmtEntier(v);
    case 'pct':
      return fmtPct(v);
    case 'date':
      return fmtDate(v);
    case 'atteinte': {
      // Taux d'atteinte de l'objectif : barre de progression et %. Le trait
      // marque l'avancement attendu à date (mois en cours).
      if (estVide(v)) return '—';
      const attendu = ligne.attendu_pct ?? 100;
      const niveau = v >= 100 ? 'atteint' : v >= attendu ? 'avance' : v >= attendu * 0.8 ? 'proche' : 'retard';
      return (
        <span className={`atteinte atteinte-${niveau}`}>
          <span className="atteinte-piste">
            <span className="atteinte-barre" style={{ width: `${Math.min(100, v)}%` }} />
            {attendu < 100 && <span className="atteinte-attendu" style={{ left: `${attendu}%` }} />}
          </span>
          <span className="atteinte-valeur">{fmtPct(v)}</span>
        </span>
      );
    }
    case 'variation': {
      // Variation en % dans une pastille : rouge (baisse), orange, vert (hausse).
      if (estVide(v)) return '—';
      const niveau = v <= -30 ? 'chute' : v <= -10 ? 'baisse' : v >= 10 ? 'hausse' : 'stable';
      return <span className={`pill-var pill-${niveau}`}>{`${v > 0 ? '+' : ''}${fmtPct(v)}`}</span>;
    }
    case 'statut':
      return estVide(v) ? '—' : (
        <StatusBadge
          code={ligne[col.code] ?? v}
          libelle={col.code === col.cle ? undefined : v}
          infobulle={col.infobulle ? ligne[col.infobulle] : undefined}
        />
      );
    default:
      return estVide(v) ? '—' : String(v);
  }
}

function comparer(a, b, numerique) {
  if (estVide(a) && estVide(b)) return 0;
  if (estVide(a)) return 1;
  if (estVide(b)) return -1;
  if (numerique) return Number(a) - Number(b);
  return String(a).localeCompare(String(b), 'fr', { numeric: true, sensitivity: 'base' });
}

/** Valeur d'une cellule telle que le filtre la retient ('' pour une cellule vide). */
const cleValeur = (v) => (estVide(v) ? '' : String(v));

/** Texte d'une valeur dans la liste du filtre, écrit comme dans le tableau. */
function libelleValeur(col, v) {
  if (estVide(v)) return '(Vides)';
  switch (col.type) {
    case 'montant':
      return fmtMontant(v);
    case 'entier':
      return fmtEntier(v);
    case 'pct':
      return fmtPct(v);
    case 'date':
      return fmtDate(v);
    case 'statut':
      return LIBELLES_STATUT[v] || String(v);
    default:
      return String(v);
  }
}

function IconeFiltre({ actif }) {
  return actif ? (
    <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
      <path d="M1 1.5h10L7.2 6.3v4.2L4.8 9.3v-3z" fill="currentColor" />
    </svg>
  ) : (
    <svg viewBox="0 0 12 12" width="11" height="11" aria-hidden="true">
      <path d="M2.5 4.5 6 8l3.5-3.5" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

/**
 * Menu de filtre d'une colonne, comme dans Excel : tri, recherche, case
 * « Sélectionner tout » et liste des valeurs à cocher.
 */
function MenuFiltre({ col, valeurs, selection, ancre, tri, onTrier, onAppliquer, onEffacer, onFermer }) {
  const ref = useRef(null);
  const [texte, setTexte] = useState('');
  const depart = () => new Set(selection ? valeurs.filter((v) => selection.has(v.cle)).map((v) => v.cle) : valeurs.map((v) => v.cle));
  const [coche, setCoche] = useState(depart);
  const [position, setPosition] = useState(null);
  const champ = useRef(null);

  const correspond = (q) => (v) => v.libelle.toLowerCase().includes(q) || v.cle.toLowerCase().includes(q);
  const q = texte.trim().toLowerCase();
  const trouvees = useMemo(() => (q ? valeurs.filter(correspond(q)) : valeurs), [valeurs, q]);
  const listees = trouvees.slice(0, MAX_VALEURS);

  // Le menu est placé sous l'en-tête, sans dépasser de la fenêtre. Il suit
  // l'en-tête quand la page ou le tableau défile, et se ferme si l'en-tête
  // n'est plus visible.
  const placer = useCallback(() => {
    const r = ancre.getBoundingClientRect();
    const cadre = ancre.closest('.tableau-defilement')?.getBoundingClientRect();
    const cache =
      r.bottom < 0 || r.top > window.innerHeight || (cadre && (r.right < cadre.left || r.left > cadre.right));
    if (cache) {
      onFermer();
      return;
    }
    const gauche = Math.max(8, Math.min(r.right - LARGEUR_MENU, window.innerWidth - LARGEUR_MENU - 8));
    const haut = Math.min(r.bottom + 4, Math.max(8, window.innerHeight - 380));
    setPosition({ left: gauche, top: haut, maxHeight: window.innerHeight - haut - 8 });
  }, [ancre, onFermer]);
  useLayoutEffect(placer, [placer]);

  // Le curseur va dans la recherche dès que le menu est en place.
  useEffect(() => {
    if (position) champ.current?.focus({ preventScroll: true });
  }, [position]);

  useEffect(() => {
    const dehors = (e) => {
      if (ref.current && !ref.current.contains(e.target) && !ancre.contains(e.target)) onFermer();
    };
    const touche = (e) => e.key === 'Escape' && onFermer();
    const defile = (e) => {
      if (ref.current && !ref.current.contains(e.target)) placer();
    };
    document.addEventListener('mousedown', dehors);
    document.addEventListener('keydown', touche);
    window.addEventListener('resize', placer);
    window.addEventListener('scroll', defile, true);
    return () => {
      document.removeEventListener('mousedown', dehors);
      document.removeEventListener('keydown', touche);
      window.removeEventListener('resize', placer);
      window.removeEventListener('scroll', defile, true);
    };
  }, [ancre, onFermer, placer]);

  // Comme dans Excel : taper un texte coche d'office les valeurs trouvées.
  const chercher = (t) => {
    setTexte(t);
    const qq = t.trim().toLowerCase();
    setCoche(qq ? new Set(valeurs.filter(correspond(qq)).map((v) => v.cle)) : depart());
  };

  const nbCochees = trouvees.reduce((n, v) => n + (coche.has(v.cle) ? 1 : 0), 0);
  const tout = trouvees.length > 0 && nbCochees === trouvees.length;
  const caseTout = useRef(null);
  useEffect(() => {
    if (caseTout.current) caseTout.current.indeterminate = nbCochees > 0 && !tout;
  }, [nbCochees, tout]);

  const basculerTout = () =>
    setCoche((c) => {
      const n = new Set(c);
      trouvees.forEach((v) => (tout ? n.delete(v.cle) : n.add(v.cle)));
      return n;
    });
  const basculer = (cle) =>
    setCoche((c) => {
      const n = new Set(c);
      if (n.has(cle)) n.delete(cle);
      else n.add(cle);
      return n;
    });

  const valider = () => {
    const retenues = trouvees.filter((v) => coche.has(v.cle)).map((v) => v.cle);
    if (retenues.length === 0) return;
    if (retenues.length === valeurs.length) onEffacer();
    else onAppliquer(new Set(retenues));
  };

  return createPortal(
    <div
      ref={ref}
      className="filtre-menu"
      role="dialog"
      aria-label={`Filtrer la colonne ${col.libelle}`}
      style={position ? { ...position, width: LARGEUR_MENU } : { visibility: 'hidden', width: LARGEUR_MENU }}
    >
      <div className="filtre-tris">
        <button type="button" className={`filtre-action${tri === 1 ? ' actif' : ''}`} onClick={() => onTrier(1)}>
          {NUMERIQUES.has(col.type) || col.type === 'date' ? 'Trier du plus petit au plus grand' : 'Trier de A à Z'}
        </button>
        <button type="button" className={`filtre-action${tri === -1 ? ' actif' : ''}`} onClick={() => onTrier(-1)}>
          {NUMERIQUES.has(col.type) || col.type === 'date' ? 'Trier du plus grand au plus petit' : 'Trier de Z à A'}
        </button>
        <button type="button" className="filtre-action" disabled={!selection} onClick={onEffacer}>
          Effacer le filtre de « {col.libelle} »
        </button>
      </div>
      <input
        type="search"
        className="champ filtre-recherche"
        placeholder="Rechercher"
        value={texte}
        ref={champ}
        onChange={(e) => chercher(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && valider()}
        aria-label={`Rechercher une valeur de ${col.libelle}`}
      />
      <div className="filtre-liste">
        {trouvees.length === 0 ? (
          <p className="filtre-vide">Aucune valeur.</p>
        ) : (
          <>
            <label className="filtre-ligne filtre-tout">
              <input type="checkbox" ref={caseTout} checked={tout} onChange={basculerTout} />
              <span>{q ? '(Sélectionner tous les résultats)' : '(Sélectionner tout)'}</span>
            </label>
            {listees.map((v) => (
              <label key={v.cle} className="filtre-ligne">
                <input type="checkbox" checked={coche.has(v.cle)} onChange={() => basculer(v.cle)} />
                <span className="filtre-valeur">{v.libelle}</span>
                <span className="filtre-nb">{fmtEntier(v.nb)}</span>
              </label>
            ))}
            {trouvees.length > listees.length && (
              <p className="filtre-vide">
                {fmtEntier(trouvees.length)} valeurs : seules les {fmtEntier(listees.length)} premières sont listées.
                Utilisez la recherche pour en trouver une autre.
              </p>
            )}
          </>
        )}
      </div>
      <div className="filtre-pied">
        <button type="button" className="btn btn-petit btn-principal" disabled={nbCochees === 0} onClick={valider}>
          OK
        </button>
        <button type="button" className="btn btn-petit" onClick={onFermer}>
          Annuler
        </button>
      </div>
    </div>,
    document.body
  );
}

/**
 * `totalLignes` et `totaux` : fournis par le serveur quand il n'envoie qu'une
 * partie des lignes ; le pied du tableau décrit alors toute la sélection.
 * Chaque colonne a un filtre comme dans Excel (liste des valeurs à cocher).
 * `totalSi` : si fourni, seules les lignes qui le vérifient entrent dans le
 * total (reçus validés, par exemple) ; `libelleTotal` nomme alors la ligne.
 * `classeLigne` : fonction qui donne une classe CSS à une ligne (mise en avant).
 */
export default function DataTable({
  colonnes,
  lignes,
  recherche = '',
  chargement = false,
  attente = null,
  onLigne,
  aideLigne,
  totalLignes = null,
  totaux: totauxServeur = null,
  totalSi = null,
  libelleTotal = 'Total',
  classeLigne = null,
}) {
  const [tri, setTri] = useState({ cle: null, sens: 1 });
  const [page, setPage] = useState(0);
  // Filtres par colonne : clé de la colonne -> valeurs gardées.
  const [filtresCol, setFiltresCol] = useState({});
  // Menu ouvert : { cle, ancre } (ancre = bouton de l'en-tête).
  const [menu, setMenu] = useState(null);

  // Un autre jeu de colonnes : les filtres de l'ancien n'ont plus de sens.
  const signature = colonnes.map((c) => c.cle).join('|');
  useEffect(() => {
    setFiltresCol({});
    setMenu(null);
  }, [signature]);

  const cherchees = useMemo(() => {
    const q = recherche.trim().toLowerCase();
    if (!q) return lignes;
    return lignes.filter((l) =>
      colonnes.some((c) => !estVide(l[c.cle]) && String(l[c.cle]).toLowerCase().includes(q))
    );
  }, [lignes, recherche, colonnes]);

  const actifs = useMemo(() => colonnes.filter((c) => filtresCol[c.cle]), [colonnes, filtresCol]);

  const filtrees = useMemo(() => {
    if (actifs.length === 0) return cherchees;
    return cherchees.filter((l) => actifs.every((c) => filtresCol[c.cle].has(cleValeur(l[c.cle]))));
  }, [cherchees, actifs, filtresCol]);

  // Valeurs proposées par le menu ouvert : celles des lignes qui passent les
  // filtres des autres colonnes, comme dans Excel.
  const colonneMenu = menu ? colonnes.find((c) => c.cle === menu.cle) : null;
  const valeursMenu = useMemo(() => {
    if (!colonneMenu) return [];
    const autres = actifs.filter((c) => c.cle !== colonneMenu.cle);
    const compte = new Map();
    for (const l of cherchees) {
      if (!autres.every((c) => filtresCol[c.cle].has(cleValeur(l[c.cle])))) continue;
      const v = l[colonneMenu.cle];
      const cle = cleValeur(v);
      const deja = compte.get(cle);
      if (deja) deja.nb += 1;
      else compte.set(cle, { cle, brut: estVide(v) ? null : v, nb: 1 });
    }
    const numerique = NUMERIQUES.has(colonneMenu.type);
    return [...compte.values()]
      .sort((a, b) => comparer(a.brut, b.brut, numerique))
      .map((v) => ({ cle: v.cle, nb: v.nb, libelle: libelleValeur(colonneMenu, v.brut) }));
  }, [colonneMenu, cherchees, actifs, filtresCol]);

  const triees = useMemo(() => {
    if (!tri.cle) return filtrees;
    const col = colonnes.find((c) => c.cle === tri.cle);
    const numerique = col && NUMERIQUES.has(col.type);
    return [...filtrees].sort((a, b) => tri.sens * comparer(a[tri.cle], b[tri.cle], numerique));
  }, [filtrees, tri, colonnes]);

  const totaux = useMemo(() => {
    const t = {};
    // Avec un filtre de colonne, le total est celui des lignes filtrées.
    const duServeur = actifs.length === 0 ? totauxServeur : null;
    const comptees = totalSi ? filtrees.filter(totalSi) : filtrees;
    colonnes.filter((c) => c.total).forEach((c) => {
      t[c.cle] =
        duServeur && duServeur[c.cle] != null
          ? duServeur[c.cle]
          : comptees.reduce((s, l) => s + (Number(l[c.cle]) || 0), 0);
    });
    return t;
  }, [filtrees, colonnes, totauxServeur, actifs, totalSi]);

  const nbPages = Math.max(1, Math.ceil(triees.length / TAILLE_PAGE));
  useEffect(() => {
    setPage(0);
  }, [recherche, lignes, tri, filtresCol]);
  const pageCourante = Math.min(page, nbPages - 1);
  const visibles = triees.slice(pageCourante * TAILLE_PAGE, (pageCourante + 1) * TAILLE_PAGE);

  const changerTri = (cle) =>
    setTri((t) => (t.cle === cle ? (t.sens === 1 ? { cle, sens: -1 } : { cle: null, sens: 1 }) : { cle, sens: 1 }));

  const fermerMenu = useMemo(() => () => setMenu(null), []);
  const appliquer = (cle, valeurs) => {
    setFiltresCol((f) => ({ ...f, [cle]: valeurs }));
    setMenu(null);
  };
  const effacer = (cle) => {
    setFiltresCol((f) => {
      const n = { ...f };
      delete n[cle];
      return n;
    });
    setMenu(null);
  };

  const aDesTotaux = colonnes.some((c) => c.total);
  const partiel = totalLignes != null && totalLignes > lignes.length;

  let decompte;
  if (partiel && actifs.length > 0) {
    decompte = `${fmtEntier(filtrees.length)} ligne${filtrees.length > 1 ? 's' : ''} (filtre appliqué aux ${fmtEntier(
      lignes.length
    )} lignes affichées, sur ${fmtEntier(totalLignes)})`;
  } else if (partiel) {
    decompte = `${fmtEntier(filtrees.length)} lignes affichées sur ${fmtEntier(totalLignes)}`;
  } else {
    decompte = `${fmtEntier(filtrees.length)} ligne${filtrees.length > 1 ? 's' : ''}${
      filtrees.length !== lignes.length ? ` sur ${fmtEntier(lignes.length)}` : ''
    }`;
  }

  return (
    <div className={`tableau-bloc${chargement ? ' en-chargement' : ''}`}>
      {actifs.length > 0 && (
        <div className="filtres-actifs" role="status">
          <span>
            Filtre sur : {actifs.map((c) => c.libelle).join(', ')} · {fmtEntier(filtrees.length)} ligne
            {filtrees.length > 1 ? 's' : ''} sur {fmtEntier(cherchees.length)}
          </span>
          <button type="button" className="btn btn-petit" onClick={() => setFiltresCol({})}>
            Effacer les filtres
          </button>
        </div>
      )}
      <div className="tableau-defilement">
        <table className="tableau">
          <thead>
            <tr>
              {colonnes.map((c) => {
                const filtre = Boolean(filtresCol[c.cle]);
                const ouvert = menu?.cle === c.cle;
                return (
                  <th
                    key={c.cle}
                    className={NUMERIQUES.has(c.type) ? 'num' : ''}
                    aria-sort={tri.cle === c.cle ? (tri.sens === 1 ? 'ascending' : 'descending') : 'none'}
                  >
                    <div className="th-contenu">
                      <button type="button" className="tri" onClick={() => changerTri(c.cle)}>
                        <span>{c.libelle}</span>
                        <span className="tri-fleche" aria-hidden="true">
                          {tri.cle === c.cle ? (tri.sens === 1 ? '▲' : '▼') : ''}
                        </span>
                      </button>
                      <button
                        type="button"
                        className={`filtre-btn${filtre ? ' actif' : ''}${ouvert ? ' ouvert' : ''}`}
                        aria-label={`Filtrer la colonne ${c.libelle}${filtre ? ' (filtre actif)' : ''}`}
                        aria-haspopup="dialog"
                        aria-expanded={ouvert}
                        title={filtre ? 'Filtre actif' : 'Filtrer'}
                        onClick={(e) => setMenu(ouvert ? null : { cle: c.cle, ancre: e.currentTarget })}
                      >
                        <IconeFiltre actif={filtre} />
                      </button>
                    </div>
                  </th>
                );
              })}
            </tr>
          </thead>
          <tbody>
            {visibles.map((l, i) => (
              <tr
                key={`${pageCourante}-${i}`}
                className={`${onLigne ? 'cliquable' : ''} ${classeLigne ? classeLigne(l) : ''}`.trim()}
                title={onLigne ? aideLigne : undefined}
                onClick={onLigne ? () => onLigne(l) : undefined}
              >
                {colonnes.map((c) => (
                  <td key={c.cle} className={NUMERIQUES.has(c.type) ? 'num' : ''}>
                    {afficher(c, l)}
                  </td>
                ))}
              </tr>
            ))}
            {visibles.length === 0 && (
              <tr>
                <td className="vide" colSpan={colonnes.length}>
                  {chargement ? attente || 'Chargement…' : 'Aucune ligne pour cette sélection.'}
                </td>
              </tr>
            )}
          </tbody>
          {aDesTotaux && filtrees.length > 0 && (
            <tfoot>
              <tr>
                {colonnes.map((c, i) => (
                  <td key={c.cle} className={NUMERIQUES.has(c.type) ? 'num' : ''}>
                    {i === 0
                      ? libelleTotal
                      : c.total
                        ? c.type === 'entier'
                          ? fmtEntier(totaux[c.cle])
                          : fmtMontant(totaux[c.cle])
                        : ''}
                  </td>
                ))}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
      <div className="tableau-pied">
        <span>{decompte}</span>
        {nbPages > 1 && (
          <span className="pagination">
            <button type="button" className="btn btn-petit" disabled={pageCourante === 0} onClick={() => setPage(pageCourante - 1)}>
              Précédent
            </button>
            <span>
              Page {pageCourante + 1} sur {nbPages}
            </span>
            <button
              type="button"
              className="btn btn-petit"
              disabled={pageCourante >= nbPages - 1}
              onClick={() => setPage(pageCourante + 1)}
            >
              Suivant
            </button>
          </span>
        )}
      </div>
      {menu && colonneMenu && (
        <MenuFiltre
          key={menu.cle}
          col={colonneMenu}
          valeurs={valeursMenu}
          selection={filtresCol[menu.cle] ?? null}
          ancre={menu.ancre}
          tri={tri.cle === menu.cle ? tri.sens : 0}
          onTrier={(sens) => {
            setTri({ cle: menu.cle, sens });
            setMenu(null);
          }}
          onAppliquer={(valeurs) => appliquer(menu.cle, valeurs)}
          onEffacer={() => effacer(menu.cle)}
          onFermer={fermerMenu}
        />
      )}
    </div>
  );
}
