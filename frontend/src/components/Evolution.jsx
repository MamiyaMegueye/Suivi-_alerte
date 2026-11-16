import { useState } from 'react';
import { urlExportComparaisonMois, urlExportEvolution, urlExportObjectifs } from '../api.js';
import { fmtMontant, fmtPct } from '../format.js';
import { useData } from '../hooks/useData.js';
import DataTable from './DataTable.jsx';
import GrapheAnnee from './GrapheAnnee.jsx';
import Top10 from './Top10.jsx';

/** Classe de la variation : baisse forte, baisse, hausse. */
function classeVariation(v) {
  if (v === null || v === undefined) return '';
  if (v <= -30) return 'var-chute';
  if (v <= -10) return 'var-baisse';
  if (v >= 10) return 'var-hausse';
  return '';
}

function Variation({ v }) {
  if (v === null || v === undefined) return <span className="var">—</span>;
  return (
    <span className={`var ${classeVariation(v)}`}>
      {v > 0 ? '+' : ''}
      {fmtPct(v)}
    </span>
  );
}

function Ligne({ libelle, nom, donnees, total }) {
  return (
    <tr className={total ? 'ligne-total' : ''}>
      <th scope="row" className="col-centre">
        {libelle}
        {nom ? <span className="nom-centre">{nom}</span> : null}
      </th>
      {donnees.valeurs.map((v) => (
        <td key={v.mois} className="num cellule-mois">
          <span className="montant">{fmtMontant(v.encaisse)}</span>
          <Variation v={v.variation_pct} />
        </td>
      ))}
      <td className="num cellule-mois">
        <span className="montant">{fmtMontant(donnees.total)}</span>
      </td>
      <td className="num cellule-mois">
        <Variation v={donnees.evolution_pct} />
      </td>
    </tr>
  );
}

/** Paramètres en plus : zone et clients (privés / État) quand ils sont choisis. */
function extraFiltres(filtres, base) {
  const extra = { ...base };
  if (filtres.zone && filtres.zone !== 'tous') extra.zone = filtres.zone;
  if (filtres.segment && filtres.segment !== 'tous') extra.segment = filtres.segment;
  return extra;
}

/** Tableau d'un mois comparé au mois précédent, par centre. */
function ComparaisonMois({ filtres, version, recherche, mois }) {
  const { data, chargement, erreur, attente } = useData(
    'encaissements/evolution',
    { centre: filtres.centre },
    version,
    extraFiltres(filtres, { mois })
  );
  if (erreur && !data) return <p className="alerte">{erreur}</p>;
  const m = data?.libelle ?? 'Mois';
  const p = data?.libelle_precedent ?? 'Mois précédent';
  const colonnes = [
    { cle: 'centre', libelle: 'Centre', type: 'texte' },
    { cle: 'nom_centre', libelle: 'Nom du centre', type: 'texte' },
    { cle: 'encaisse_precedent', libelle: `${p}${data?.en_cours ? ` (au ${data.jour_max})` : ''}`, type: 'montant', total: true },
    { cle: 'encaisse', libelle: `${m}${data?.en_cours ? ` (au ${data.jour_max})` : ''}`, type: 'montant', total: true },
    { cle: 'ecart', libelle: 'Écart', type: 'montant', total: true },
    { cle: 'variation_pct', libelle: 'Variation (%)', type: 'variation' },
    { cle: 'part_pct', libelle: 'Part du total (%)', type: 'pct' },
  ];
  return (
    <>
      {attente && (
        <p className="info-chargement" role="status">
          {attente}
        </p>
      )}
      {data && (
        <DataTable
          colonnes={colonnes}
          lignes={data.lignes}
          recherche={recherche}
          chargement={chargement}
          classeLigne={(l) => classeVariation(l.variation_pct)}
        />
      )}
    </>
  );
}

/** Encaissé du mois de chaque centre comparé à son objectif mensuel. */
function ObjectifsMois({ filtres, version, recherche, mois }) {
  const { data, chargement, erreur, attente } = useData(
    'encaissements/objectifs',
    { centre: filtres.centre },
    version,
    filtres.zone && filtres.zone !== 'tous' ? { mois, zone: filtres.zone } : { mois }
  );
  if (erreur && !data) return <p className="alerte">{erreur}</p>;
  const auJour = data?.en_cours ? ` (au ${data.jour})` : '';
  const colonnes = [
    { cle: 'zone', libelle: 'Zone', type: 'texte' },
    { cle: 'centre', libelle: 'Centre', type: 'texte' },
    { cle: 'nom_centre', libelle: 'Nom du centre', type: 'texte' },
    { cle: 'objectif', libelle: `Objectif ${data?.libelle ?? ''}`, type: 'montant', total: true },
    { cle: 'encaisse', libelle: `Encaissé privé${auJour}`, type: 'montant', total: true },
    { cle: 'taux_atteinte', libelle: 'Atteinte de l’objectif', type: 'atteinte' },
    { cle: 'reste', libelle: 'Reste à encaisser', type: 'montant', total: true },
    { cle: 'etat', libelle: 'État', type: 'montant', total: true },
    { cle: 'total', libelle: 'Total (privé + État)', type: 'montant', total: true },
    { cle: 'statut', libelle: 'Statut', type: 'texte' },
  ];
  const t = data?.totaux;
  return (
    <>
      {attente && (
        <p className="info-chargement" role="status">
          {attente}
        </p>
      )}
      {t && (
        <div className="objectif-global">
          <div className="objectif-global-chiffres">
            <span className="objectif-global-libelle">
              {data.centre ? `Centre ${data.centre}` : 'Ensemble'} · {data.libelle}
              {auJour}
            </span>
            <strong>{fmtPct(t.taux_atteinte)}</strong>
            <span>
              {fmtMontant(Math.round(t.encaisse))} / {fmtMontant(Math.round(t.objectif))} MRU
            </span>
          </div>
          <span className="atteinte atteinte-grande">
            <span className="atteinte-piste">
              <span className="atteinte-barre" style={{ width: `${Math.min(100, t.taux_atteinte ?? 0)}%` }} />
              {data.en_cours && <span className="atteinte-attendu" style={{ left: `${data.attendu_pct}%` }} />}
            </span>
          </span>
          {data.en_cours && (
            <span className="objectif-global-attendu">Attendu au {data.jour} : {fmtPct(data.attendu_pct)}</span>
          )}
        </div>
      )}
      {data && (
        <DataTable
          colonnes={colonnes}
          lignes={data.lignes}
          recherche={recherche}
          chargement={chargement}
        />
      )}
    </>
  );
}

/** Vrai si la période du haut couvre un seul mois, à partir du 1er. */
function moisFiltre(filtres) {
  const d = filtres.dateDebut || '';
  const f = filtres.dateFin || '';
  return d.slice(0, 7) === f.slice(0, 7) && d.endsWith('-01') ? d.slice(0, 7) : null;
}

/**
 * Onglet Encaissements, vue « évolution » : si un mois est filtré en haut, sa
 * comparaison au mois précédent par centre ; sinon (ou au choix) l'évolution
 * de chaque mois sur toute l'année.
 */
export default function Evolution({ filtres, version, recherche }) {
  const mois = moisFiltre(filtres);
  const [vue, setVue] = useState('mois');
  // Sans mois filtré en haut, la comparaison au mois précédent n'existe pas.
  const choix = !mois && vue === 'mois' ? 'annee' : vue;
  const annuelle = choix === 'annee';
  const annee = (filtres.dateFin || '').slice(0, 4);
  // Mois des objectifs : celui filtré en haut, sinon celui de la date de fin.
  const moisObjectifs = mois || (filtres.dateFin || '').slice(0, 7);
  return (
    <div className="evolution">
      <div className="outils-evolution">
        <div className="segment" role="group" aria-label="Comparaison">
          {mois && (
            <button
              type="button"
              className={`segment-btn${choix === 'mois' ? ' actif' : ''}`}
              aria-pressed={choix === 'mois'}
              onClick={() => setVue('mois')}
            >
              Mois / mois précédent
            </button>
          )}
          <button
            type="button"
            className={`segment-btn${choix === 'annee' ? ' actif' : ''}`}
            aria-pressed={choix === 'annee'}
            onClick={() => setVue('annee')}
          >
            Évolution sur l’année {annee}
          </button>
          <button
            type="button"
            className={`segment-btn${choix === 'objectifs' ? ' actif' : ''}`}
            aria-pressed={choix === 'objectifs'}
            onClick={() => setVue('objectifs')}
          >
            Objectifs du mois
          </button>
        </div>
        <a
          className="btn"
          href={
            choix === 'objectifs'
              ? urlExportObjectifs(filtres, moisObjectifs)
              : annuelle
                ? urlExportEvolution(filtres)
                : urlExportComparaisonMois(filtres, mois)
          }
          download
        >
          Excel
        </a>
      </div>
      {choix === 'objectifs' ? (
        <ObjectifsMois filtres={filtres} version={version} recherche={recherche} mois={moisObjectifs} />
      ) : annuelle ? (
        <EvolutionAnnee filtres={filtres} version={version} />
      ) : (
        <ComparaisonMois filtres={filtres} version={version} recherche={recherche} mois={mois} />
      )}
    </div>
  );
}

/**
 * Évolution des encaissements sur l'année : pour chaque centre, l'encaissé de
 * chaque mois et sa variation sur le mois précédent, le total de l'année et
 * l'évolution du dernier mois complet par rapport à janvier. L'année et le
 * centre sont ceux des filtres du haut de la page.
 */
function EvolutionAnnee({ filtres, version }) {
  const annee = (filtres.dateFin || '').slice(0, 4);
  // Période du top 10 : toute l'année ou un mois.
  const [periodeTop, setPeriodeTop] = useState('annee');
  const { data, chargement, erreur, attente } = useData(
    'encaissements/annee',
    { centre: filtres.centre },
    version,
    extraFiltres(filtres, { annee })
  );

  if (erreur && !data) return <p className="alerte">{erreur}</p>;
  // Libellés de la période des classements.
  const moisLibelle = (k) => data?.mois.find((m) => m.mois === k)?.libelle ?? '';
  const indexTop = data ? data.mois.findIndex((m) => m.mois === periodeTop) : -1;
  const enCoursTop = data?.jour_en_cours && indexTop === (data?.mois.length ?? 0) - 1;
  const libellePeriode =
    periodeTop === 'annee' ? `année ${data?.annee}` : `${moisLibelle(periodeTop)} ${data?.annee}${enCoursTop ? ` (au ${data.jour_en_cours})` : ''}`;
  return (
    <>
      {attente && (
        <p className="info-chargement" role="status">
          {attente}
        </p>
      )}
      {/* La courbe n'est affichée que pour un centre choisi en haut. */}
      {data && data.centre && (
        <GrapheAnnee
          titre={`Encaissé par mois en ${data.annee} · ${
            data.centre
              ? `centre ${data.centre}${data.lignes[0]?.nom_centre ? ` ${data.lignes[0].nom_centre}` : ''}`
              : 'tous les centres'
          }`}
          valeurs={data.centre ? (data.lignes[0]?.valeurs ?? []) : data.total.valeurs}
          mois={data.mois}
          jourEnCours={data.jour_en_cours}
        />
      )}
      {data && !data.centre && (
        <section className="tops" aria-label="Classements des centres">
          <div className="tops-outils">
            <select
              className="champ top10-choix"
              value={periodeTop}
              onChange={(e) => setPeriodeTop(e.target.value)}
              aria-label="Période des classements"
            >
              <option value="annee">Toute l’année {data.annee}</option>
              {data.mois.map((m) => (
                <option key={m.mois} value={m.mois}>
                  {m.libelle} {data.annee}
                </option>
              ))}
            </select>
          </div>
          <div className="tops-grille">
            <Top10
              titre={`Les 10 centres qui encaissent le plus · ${libellePeriode}`}
              lignes={data.lignes.map((l) => ({
                centre: l.centre,
                nom_centre: l.nom_centre,
                montant: periodeTop === 'annee' ? l.total : (l.valeurs.find((v) => v.mois === periodeTop)?.encaisse ?? 0),
              }))}
              total={
                periodeTop === 'annee'
                  ? data.total.total
                  : (data.total.valeurs.find((v) => v.mois === periodeTop)?.encaisse ?? 0)
              }
            />
            <Top10
              moins
              titre={`Les 10 centres qui encaissent le moins · ${libellePeriode}`}
              lignes={data.lignes.map((l) => ({
                centre: l.centre,
                nom_centre: l.nom_centre,
                montant: periodeTop === 'annee' ? l.total : (l.valeurs.find((v) => v.mois === periodeTop)?.encaisse ?? 0),
              }))}
              total={
                periodeTop === 'annee'
                  ? data.total.total
                  : (data.total.valeurs.find((v) => v.mois === periodeTop)?.encaisse ?? 0)
              }
            />
          </div>
        </section>
      )}
      {data && (
        <div className={`tableau-bloc${chargement ? ' en-chargement' : ''}`}>
          <div className="tableau-defilement">
            <table className="tableau tableau-annee">
              <thead>
                <tr>
                  <th className="col-centre">Centre</th>
                  {data.mois.map((m, i) => (
                    <th key={m.mois} className="num">
                      {m.libelle} {data.annee}
                      {data.jour_en_cours && i === data.mois.length - 1 ? ` (au ${data.jour_en_cours})` : ''}
                    </th>
                  ))}
                  <th className="num">Total {data.annee}</th>
                  <th className="num">
                    Évolution
                    {data.dernier_complet ? ` janv. → ${data.mois.find((m) => m.mois === data.dernier_complet)?.libelle}` : ''}
                  </th>
                </tr>
              </thead>
              <tbody>
                {data.lignes.map((l) => (
                  <Ligne key={l.centre} libelle={l.centre} nom={l.nom_centre} donnees={l} />
                ))}
              </tbody>
              {!data.centre && (
                <tfoot>
                  <Ligne libelle={filtres.zone === "nkt" ? "Total Nouakchott" : filtres.zone === "nkt_ndb" ? "Total Nouakchott + Nouadhibou" : "Tous les centres"} donnees={data.total} total />
                </tfoot>
              )}
            </table>
          </div>
        </div>
      )}
    </>
  );
}
