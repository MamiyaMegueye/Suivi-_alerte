import { useMemo, useState } from 'react';
import { comparerFichier } from '../api.js';
import { COLONNES } from '../datasets.js';
import { fmtEntier, fmtMontant, fmtPct } from '../format.js';
import DataTable from './DataTable.jsx';
import StatusBadge from './StatusBadge.jsx';

export const COMPARAISON_VIDE = {
  fichier: null,
  tolerance: 5,
  etatId: '',
  resultat: null,
  chargement: false,
  erreur: null,
  filtre: 'TOUS',
};

const TOLERANCES = [0, 2, 5, 10, 15];

function telecharger(blob, nom) {
  const url = URL.createObjectURL(blob);
  const lien = document.createElement('a');
  lien.href = url;
  lien.download = nom;
  document.body.appendChild(lien);
  lien.click();
  lien.remove();
  URL.revokeObjectURL(url);
}

/**
 * Import du fichier d'état de rapprochement et comparaison avec le CRM.
 * L'état est conservé par la page pour ne pas être perdu en changeant d'onglet.
 */
export default function Comparaison({ etat, setEtat, recherche }) {
  const [exportEnCours, setExportEnCours] = useState(false);
  const { fichier, tolerance, etatId, resultat, chargement, erreur, filtre } = etat;

  const lancer = async (f, tol, id) => {
    if (!f) return;
    setEtat((e) => ({ ...e, fichier: f, tolerance: tol, etatId: id, chargement: true, erreur: null }));
    try {
      const r = await comparerFichier(f, { tolerance: tol, etat: id });
      setEtat((e) => ({ ...e, resultat: r, chargement: false, filtre: 'TOUS' }));
    } catch (err) {
      setEtat((e) => ({ ...e, chargement: false, erreur: err.message }));
    }
  };

  const exporter = async () => {
    setExportEnCours(true);
    try {
      const blob = await comparerFichier(fichier, { tolerance, etat: etatId, format: 'xlsx' });
      telecharger(blob, 'comparaison_rapprochement.xlsx');
    } catch (err) {
      setEtat((e) => ({ ...e, erreur: err.message }));
    } finally {
      setExportEnCours(false);
    }
  };

  const lignes = useMemo(() => {
    if (!resultat) return [];
    return filtre === 'TOUS' ? resultat.lignes : resultat.lignes.filter((l) => l.resultat_code === filtre);
  }, [resultat, filtre]);

  const selection = resultat?.etats.filter((e) => e.selectionne) ?? [];
  const lectureDouteuse = selection.some((e) => e.lecture_conforme === false);

  return (
    <div className="comparaison">
      <div className="sous-entete">
        <h3>Comparer avec un fichier de rapprochement</h3>
      </div>
      <p className="panneau-description">
        Chaque versement d’espèces et chaque chèque du fichier est cherché dans le CRM sur le compte bancaire, le
        montant et la date. La recherche suit les dates du fichier, pas la période choisie en haut de page.
      </p>

      <div className="comparaison-outils">
        <label className="btn btn-fichier">
          {fichier ? 'Changer de fichier' : 'Choisir le fichier Excel'}
          <input
            type="file"
            accept=".xlsx,.xlsm"
            onChange={(e) => {
              const f = e.target.files?.[0];
              e.target.value = '';
              if (f) lancer(f, tolerance, '');
            }}
          />
        </label>
        {fichier && <span className="fichier-nom">{fichier.name}</span>}
        <label className="etiquette">
          Écart de date toléré
          <select
            className="champ"
            value={tolerance}
            onChange={(e) => {
              const tol = Number(e.target.value);
              if (fichier) lancer(fichier, tol, etatId);
              else setEtat((x) => ({ ...x, tolerance: tol }));
            }}
          >
            {TOLERANCES.map((t) => (
              <option key={t} value={t}>
                {t === 0 ? 'Même jour' : `${t} jours`}
              </option>
            ))}
          </select>
        </label>
        {resultat && (
          <label className="etiquette">
            État comparé
            <select className="champ" value={etatId} onChange={(e) => lancer(fichier, tolerance, e.target.value)}>
              <option value="">Le plus récent de chaque feuille</option>
              {resultat.etats.map((e) => (
                <option key={e.id} value={e.id}>
                  {e.banque} — au {e.mois} ({e.nb_lignes} lignes)
                </option>
              ))}
            </select>
          </label>
        )}
        {resultat && (
          <button type="button" className="btn" onClick={exporter} disabled={exportEnCours || chargement}>
            {exportEnCours ? 'Export…' : 'Exporter le résultat (Excel)'}
          </button>
        )}
      </div>

      {erreur && (
        <p className="alerte" role="alert">
          {erreur}
        </p>
      )}
      {chargement && !resultat && <p className="attente">Lecture du fichier et recherche dans le CRM…</p>}

      {resultat && (
        <div className={chargement ? 'en-chargement' : ''}>
          <p className="perimetre">
            {selection.map((e) => `${e.banque} au ${e.mois}, compte ${e.compte || 'non lu'}`).join(' · ')}
            {' · '}
            {fmtEntier(resultat.nb_lignes)} lignes pour {fmtMontant(resultat.montant_total)} MRU
          </p>
          {lectureDouteuse && (
            <p className="alerte">
              Les totaux relus ne redonnent pas les sous-totaux du fichier : des lignes ont pu être mal lues.
            </p>
          )}

          <div className="tuiles tuiles-resultat">
            {resultat.synthese.map((s) => (
              <button
                type="button"
                key={s.code}
                className={`tuile tuile-bouton${filtre === s.code ? ' actif' : ''}`}
                aria-pressed={filtre === s.code}
                onClick={() => setEtat((e) => ({ ...e, filtre: e.filtre === s.code ? 'TOUS' : s.code }))}
              >
                <div className="tuile-libelle">
                  <StatusBadge code={s.code} libelle={s.libelle} />
                </div>
                <div className="tuile-valeur">
                  {fmtEntier(s.nb)} <span className="tuile-unite">lignes ({fmtPct(s.pct_nb)})</span>
                </div>
                <div className="tuile-detail">
                  {fmtMontant(s.montant)} MRU ({fmtPct(s.pct_montant)})
                </div>
              </button>
            ))}
          </div>
          <p className="carte-note">
            Cliquer sur une case pour ne voir que ces lignes. « Hors périmètre » regroupe les paiements d’abonnés à la
            banque, les virements et les e-paiements, qui ne passent pas par les caisses.
          </p>

          <DataTable colonnes={COLONNES.comparaison} lignes={lignes} recherche={recherche} chargement={chargement} />
        </div>
      )}
    </div>
  );
}
