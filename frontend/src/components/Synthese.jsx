import { useState } from 'react';
import { fmtDate, fmtEntier, fmtMontant, fmtPct, pluriel } from '../format.js';
import DataTable from './DataTable.jsx';
import StatusBadge from './StatusBadge.jsx';

/** Avec `onClick`, la tuile devient un bouton qui ouvre ou ferme sa liste. */
function Tuile({ libelle, valeur, unite, detail, onClick, ouverte }) {
  const contenu = (
    <>
      <div className="tuile-libelle">{libelle}</div>
      <div className="tuile-valeur">
        {valeur}
        {unite && <span className="tuile-unite"> {unite}</span>}
      </div>
      <div className="tuile-detail">{detail}</div>
      {onClick && <div className="tuile-action">{ouverte ? 'Masquer la liste' : 'Voir la liste'}</div>}
    </>
  );
  if (!onClick) return <div className="tuile">{contenu}</div>;
  return (
    <button
      type="button"
      className={`tuile tuile-bouton${ouverte ? ' actif' : ''}`}
      aria-expanded={ouverte}
      onClick={onClick}
    >
      {contenu}
    </button>
  );
}

const COLONNES_SUR_VERSEMENTS = [
  { cle: 'centre', libelle: 'Centre', type: 'texte' },
  { cle: 'nom_centre', libelle: 'Nom du centre', type: 'texte' },
  { cle: 'jour', libelle: 'Dernier jour', type: 'date' },
  { cle: 'solde', libelle: 'Solde (sur-versement)', type: 'montant', total: true },
  { cle: 'pct', libelle: '% du total', type: 'pct' },
];

/** Centres dont le solde est négatif au dernier jour de la période. */
function ListeSurVersements({ s, onCentre, onFermer }) {
  const total = s.solde_negatif || 0;
  const lignes = (s.soldes_par_centre || [])
    .filter((x) => x.solde <= -1)
    .sort((a, b) => a.solde - b.solde)
    .map((x) => ({ ...x, pct: total ? Math.round((x.solde / total) * 1000) / 10 : null }));
  return (
    <section className="liste-tuile" aria-label="Centres en sur-versement">
      <div className="sous-entete">
        <h3>
          Sur-versements : {pluriel(lignes.length, 'centre')} sur {fmtEntier(s.centres_total)} (
          {fmtPct(s.pct_centres_solde_negatif)})
        </h3>
        <button type="button" className="btn btn-petit" onClick={onFermer}>
          Fermer
        </button>
      </div>
      <p className="panneau-description">
        Centres dont le solde est négatif au dernier jour de la période : le versé dépasse ce que la caisse avait à
        verser.
      </p>
      <DataTable
        colonnes={COLONNES_SUR_VERSEMENTS}
        lignes={lignes}
        onLigne={onCentre ? (l) => onCentre(l.centre) : undefined}
        aideLigne="Cliquer pour ne garder que ce centre"
      />
    </section>
  );
}

export function Indicateurs({ s, onCentre }) {
  const [liste, setListe] = useState(null);
  return (
    <>
    <section className="tuiles" aria-label="Indicateurs de la période">
      <Tuile
        libelle="Encaissé par les caisses"
        valeur={fmtMontant(s.encaisse)}
        unite="MRU"
        detail={`${pluriel(s.nb_recus, 'reçu')} · espèces ${fmtPct(s.pct_especes)}`}
      />
      <Tuile
        libelle="Versé en banque"
        valeur={fmtMontant(s.verse)}
        unite="MRU"
        detail={`${fmtPct(s.pct_verse)} du montant à verser (${fmtMontant(s.a_verser)})`}
      />
      <Tuile
        libelle="Solde non versé"
        valeur={fmtMontant(s.solde_positif)}
        unite="MRU"
        detail={`${pluriel(s.nb_centres_solde_positif, 'centre')} (${fmtPct(s.pct_centres_solde_positif)})`}
      />
      <Tuile
        libelle="Sur-versements"
        valeur={fmtMontant(s.solde_negatif)}
        unite="MRU"
        detail={`${pluriel(s.nb_centres_solde_negatif, 'centre')} (${fmtPct(s.pct_centres_solde_negatif)})`}
        ouverte={liste === 'sur_versements'}
        onClick={() => setListe((l) => (l === 'sur_versements' ? null : 'sur_versements'))}
      />
      <Tuile
        libelle="Ruptures de report"
        valeur={fmtEntier(s.nb_ruptures)}
        detail={`${fmtMontant(s.montant_ruptures)} MRU sortis sans versement`}
      />
      <Tuile
        libelle="Versements non validés"
        valeur={fmtMontant(s.verse_non_valide)}
        unite="MRU"
        detail={`${fmtEntier(s.nb_versements_non_valides)} sur ${pluriel(s.nb_versements, 'versement')} (${fmtPct(
          s.pct_nb_versements_non_valides
        )})`}
      />
      <Tuile
        libelle="Annulations"
        valeur={fmtMontant(s.montant_annule)}
        unite="MRU"
        detail={`${pluriel(s.nb_annulations, 'reçu')} (${fmtPct(s.pct_nb_annulations)})`}
      />
    </section>
    {liste === 'sur_versements' && (
      <ListeSurVersements s={s} onCentre={onCentre} onFermer={() => setListe(null)} />
    )}
    </>
  );
}

const NB_BARRES = 12;

/** Barres divergentes autour de zéro : solde non versé à droite, sur-versement à gauche. */
export function GraphiqueSoldes({ soldes, onCentre }) {
  const [survol, setSurvol] = useState(null);
  const lignes = soldes.filter((x) => Math.abs(x.solde) >= 1).slice(0, NB_BARRES);
  const autres = soldes.filter((x) => Math.abs(x.solde) >= 1).length - lignes.length;

  const maxPos = Math.max(0, ...lignes.map((x) => x.solde));
  const maxNeg = Math.max(0, ...lignes.map((x) => -x.solde));
  const etendue = maxPos + maxNeg || 1;
  const zero = (maxNeg / etendue) * 100;

  return (
    <figure className="carte">
      <figcaption className="carte-titre">
        <h2>Solde non versé par centre</h2>
        <p>Au dernier jour de la période, les {NB_BARRES} plus forts écarts.</p>
      </figcaption>

      {lignes.length === 0 ? (
        <p className="carte-vide">Aucun solde à signaler sur cette période.</p>
      ) : (
        <>
          <div className="legende">
            <span className="legende-item">
              <span className="pastille" style={{ background: 'var(--serie-rouge)' }} />
              Non versé (solde positif)
            </span>
            <span className="legende-item">
              <span className="pastille" style={{ background: 'var(--serie-bleu)' }} />
              Sur-versement (solde négatif)
            </span>
          </div>
          <div className="barres" onMouseLeave={() => setSurvol(null)}>
            {lignes.map((x) => {
              const positif = x.solde >= 0;
              const largeur = Math.max((Math.abs(x.solde) / etendue) * 100, 0.4);
              const style = positif
                ? { left: `${zero}%`, width: `${largeur}%`, background: 'var(--serie-rouge)' }
                : { left: `${Math.max(zero - largeur, 0)}%`, width: `${largeur}%`, background: 'var(--serie-bleu)' };
              const actif = survol === x.centre;
              return (
                <button
                  type="button"
                  key={x.centre}
                  className={`barre-ligne${actif ? ' actif' : ''}`}
                  onMouseEnter={() => setSurvol(x.centre)}
                  onFocus={() => setSurvol(x.centre)}
                  onBlur={() => setSurvol(null)}
                  onClick={() => onCentre?.(x.centre)}
                  aria-label={`Centre ${x.centre} ${x.nom_centre} : solde ${fmtMontant(x.solde)} MRU. Cliquer pour filtrer.`}
                >
                  <span className="barre-nom">
                    <span className="barre-code">{x.centre}</span> {x.nom_centre}
                  </span>
                  <span className="barre-piste">
                    <span className="barre-zero" style={{ left: `${zero}%` }} />
                    <span className={`barre ${positif ? 'positif' : 'negatif'}`} style={style} />
                  </span>
                  <span className="barre-valeur">{fmtMontant(x.solde)}</span>
                  {actif && (
                    <span className="infobulle" role="tooltip">
                      <strong>{fmtMontant(x.solde)} MRU</strong>
                      <span>
                        Centre {x.centre} — {x.nom_centre}
                      </span>
                      <span>
                        {positif ? 'Non versé' : 'Sur-versement'} au {fmtDate(x.jour)}
                      </span>
                    </span>
                  )}
                </button>
              );
            })}
          </div>
          {autres > 0 && (
            <p className="carte-note">
              {autres > 1 ? `${autres} autres centres ont` : '1 autre centre a'} un solde : voir l’onglet Contrôle par centre.
            </p>
          )}
        </>
      )}
    </figure>
  );
}

function Jauge({ libelle, pct, detail }) {
  const rempli = Math.max(0, Math.min(100, pct ?? 0));
  return (
    <div className="jauge">
      <div className="jauge-entete">
        <span>{libelle}</span>
        <strong>{fmtPct(pct)}</strong>
      </div>
      <div className="jauge-piste" role="img" aria-label={`${libelle} : ${fmtPct(pct)}`}>
        <div className="jauge-rempli" style={{ width: `${rempli}%` }} />
      </div>
      <div className="jauge-detail">{detail}</div>
    </div>
  );
}

export function Repartition({ s }) {
  const modes = [
    { cle: 'especes', libelle: 'Espèces', valeur: s.especes, pct: s.pct_especes, couleur: 'var(--serie-bleu)' },
    { cle: 'cheques', libelle: 'Chèques', valeur: s.cheques, pct: s.pct_cheques, couleur: 'var(--serie-orange)' },
    { cle: 'virements', libelle: 'Virements', valeur: s.virements, pct: s.pct_virements, couleur: 'var(--serie-vert)' },
  ];
  return (
    <figure className="carte">
      <figcaption className="carte-titre">
        <h2>Encaissement et versement</h2>
        <p>Répartition par mode de paiement, puis part réellement versée.</p>
      </figcaption>

      <div className="empile" role="img" aria-label="Répartition de l'encaissé par mode de paiement">
        {modes
          .filter((m) => m.valeur > 0)
          .map((m) => (
            <span
              key={m.cle}
              className="empile-segment"
              style={{ flexGrow: m.valeur, background: m.couleur }}
              title={`${m.libelle} : ${fmtMontant(m.valeur)} MRU (${fmtPct(m.pct)})`}
            />
          ))}
      </div>
      <table className="mini-tableau">
        <tbody>
          {modes.map((m) => (
            <tr key={m.cle}>
              <td>
                <span className="pastille" style={{ background: m.couleur }} /> {m.libelle}
              </td>
              <td className="num">{fmtMontant(m.valeur)}</td>
              <td className="num discret">{fmtPct(m.pct)}</td>
            </tr>
          ))}
        </tbody>
      </table>

      <div className="jauges">
        <Jauge
          libelle="Espèces versées"
          pct={s.pct_verse_especes}
          detail={`${fmtMontant(s.verse_especes)} sur ${fmtMontant(s.especes)} MRU`}
        />
        <Jauge
          libelle="Chèques versés"
          pct={s.pct_verse_cheques}
          detail={`${fmtMontant(s.verse_cheques)} sur ${fmtMontant(s.cheques)} MRU`}
        />
      </div>

      <h3 className="carte-soustitre">Statut des lignes centre-jour</h3>
      <table className="mini-tableau">
        <tbody>
          {s.repartition_statuts
            .filter((r) => r.nb > 0 || r.code === 'OK')
            .map((r) => (
              <tr key={r.code}>
                <td>
                  <StatusBadge code={r.code} libelle={r.libelle} />
                </td>
                <td className="num">{fmtEntier(r.nb)}</td>
                <td className="num discret">{fmtPct(r.pct)}</td>
              </tr>
            ))}
        </tbody>
      </table>
    </figure>
  );
}
