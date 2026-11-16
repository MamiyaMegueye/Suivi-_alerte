import { useMemo, useState } from 'react';
import { importerDfc, supprimerDfc, urlExportDfc } from '../api.js';
import { COLONNES } from '../datasets.js';
import { fmtDate, fmtEntier, fmtMontant, fmtPct } from '../format.js';
import { useData } from '../hooks/useData.js';
import DataTable from './DataTable.jsx';
import StatusBadge from './StatusBadge.jsx';

// Ce que signifie chaque case, affiché au-dessus de sa liste.
const EXPLICATIONS = {
  DFC_CONFORME: 'La DFC et le CRM ont le même encaissé, le même versé et la même banque ce jour-là.',
  DFC_BANQUE: 'Le montant versé est le même, mais la banque notée par la DFC n’est pas celle du versement dans le CRM.',
  DFC_DEPENSE: 'Les dépenses notées par la DFC ne sont pas celles du CRM.',
  DFC_VERSE: 'Le montant versé noté par la DFC n’est pas celui du CRM. Écart = CRM − DFC.',
  DFC_ENCAISSE: 'Le montant encaissé noté par la DFC (espèces, chèques ou virements) n’est pas celui du CRM.',
  DFC_NON_SAISI: 'Jours où la caisse a encaissé ou versé dans le CRM, mais absents de la saisie de la DFC : le caissier n’a pas envoyé ses pièces, ou la DFC ne les a pas saisies. Ils comptent pour zéro côté DFC.',
  DFC_ABSENT_CRM: 'Jours saisis par la DFC pour lesquels le CRM n’a ni encaissement ni versement.',
  TOUS: 'Tous les jours de la période, pour les centres dont un fichier de la DFC est importé.',
};

/**
 * Onglet « Croisement DFC » : import des classeurs saisis par la DFC (un par
 * centre, le numéro du centre dans le nom du fichier), puis comparaison jour
 * par jour avec le CRM sur la période affichée.
 */
export default function Dfc({ filtres, version, recherche, onCentre, onPeriode }) {
  const [imports, setImports] = useState(0);
  const [envoi, setEnvoi] = useState({ enCours: false, erreurs: [] });
  const [ajouts, setAjouts] = useState([]);
  const [confirmation, setConfirmation] = useState(false);
  // Case ouverte : sa liste s'affiche sous les cases. Au départ, tous les écarts.
  const [filtre, setFiltre] = useState('ECARTS');
  const { data, chargement, erreur, attente } = useData('dfc/croisement', filtres, version + imports);

  // Un fichier par centre : plusieurs peuvent être choisis d'un coup. Un fichier
  // refusé (nom sans numéro de centre) n'empêche pas les autres.
  const importer = async (fichiers) => {
    setEnvoi({ enCours: true, erreurs: [] });
    const faits = [];
    const erreurs = [];
    for (const fichier of fichiers) {
      try {
        const reponse = await importerDfc(fichier);
        if (reponse?.dfc?.ajout) faits.push(reponse.dfc.ajout);
      } catch (err) {
        erreurs.push(err.message);
      }
    }
    setAjouts(faits);
    setEnvoi({ enCours: false, erreurs });
    if (faits.length) setImports((n) => n + 1);
  };

  const reinitialiser = async () => {
    setConfirmation(false);
    setEnvoi({ enCours: true, erreurs: [] });
    try {
      await supprimerDfc();
      setAjouts([]);
      setEnvoi({ enCours: false, erreurs: [] });
      setImports((n) => n + 1);
    } catch (err) {
      setEnvoi({ enCours: false, erreurs: [err.message] });
    }
  };

  const dfc = data?.dfc ?? null;
  const t = data?.totaux;
  const lignes = useMemo(() => {
    const toutes = data?.lignes ?? [];
    if (filtre === 'TOUS') return toutes;
    if (filtre === 'ECARTS') {
      return toutes.filter((l) => l.resultat_code !== 'DFC_CONFORME');
    }
    return toutes.filter((l) => l.resultat_code === filtre);
  }, [data, filtre]);
  const caseOuverte = data?.synthese?.find((c) => c.code === filtre);
  const nbCentres = dfc?.centres?.length ?? 0;

  const carteEcart = (libelle, valeurDfc, valeurCrm, ecart, pct) => (
    <div className={`tuile${Math.abs(ecart) >= 1 ? ' tuile-ecart' : ''}`}>
      <div className="tuile-libelle">{libelle}</div>
      <div className="tuile-valeur">
        {ecart > 0 ? '+' : ''}
        {fmtMontant(ecart)} <span className="tuile-unite">MRU</span>
      </div>
      <div className="tuile-detail">
        CRM {fmtMontant(valeurCrm)} · DFC {fmtMontant(valeurDfc)} ({fmtPct(pct)} du CRM)
      </div>
    </div>
  );

  return (
    <div className="comparaison">
      <div className="comparaison-outils">
        <label className="btn btn-fichier">
          {envoi.enCours ? 'Lecture des fichiers…' : dfc ? 'Ajouter des fichiers de la DFC' : 'Importer les fichiers de la DFC (Excel)'}
          <input
            type="file"
            accept=".xlsx,.xlsm"
            multiple
            disabled={envoi.enCours}
            onChange={(e) => {
              const choisis = Array.from(e.target.files ?? []);
              e.target.value = '';
              if (choisis.length) importer(choisis);
            }}
          />
        </label>
        {dfc && (
          <span className="fichier-nom" title={dfc.fichiers.map((f) => `${f.centre} : ${f.nom_fichier}`).join(', ')}>
            {nbCentres > 1 ? `${nbCentres} centres` : `Centre ${dfc.centres[0]}`} · {fmtEntier(dfc.nb_jours)} jours
            saisis · du {fmtDate(dfc.date_min)} au {fmtDate(dfc.date_max)}
          </span>
        )}
        {dfc && data?.lignes && (
          <a className="btn btn-principal" href={urlExportDfc(filtres)} download>
            Exporter le croisement (Excel)
          </a>
        )}
        {dfc && !confirmation && (
          <button type="button" className="btn" disabled={envoi.enCours} onClick={() => setConfirmation(true)}>
            Réinitialiser
          </button>
        )}
        {dfc && confirmation && (
          <span className="confirmation" role="group" aria-label="Confirmer la réinitialisation">
            Effacer la saisie de la DFC importée ?
            <button type="button" className="btn btn-alerte" onClick={reinitialiser}>
              Oui, effacer
            </button>
            <button type="button" className="btn" onClick={() => setConfirmation(false)}>
              Annuler
            </button>
          </span>
        )}
      </div>

      {envoi.erreurs.map((e) => (
        <p className="alerte" role="alert" key={e}>
          {e}
        </p>
      ))}
      {erreur && !data && <p className="alerte">{erreur}</p>}
      {ajouts.map((a) => (
        <p className="info-chargement" role="status" key={a.nom_fichier}>
          {a.nom_fichier} : centre {a.centre}, {fmtEntier(a.nb_jours)} jours saisis
          {a.date_min ? ` du ${fmtDate(a.date_min)} au ${fmtDate(a.date_max)}` : ''}
        </p>
      ))}
      {attente && (
        <p className="info-chargement" role="status">
          {attente}
        </p>
      )}

      {data && !dfc && !envoi.enCours && (
        <p className="panneau-description">
          Aucun fichier de la DFC importé. Chaque fichier est le classeur d’un centre, avec une ligne par jour
          (encaissements, versements par banque, dépenses). Le nom du fichier doit contenir le numéro du centre, par
          exemple 51.xlsx. Le fichier peut contenir toute l’année ou un seul mois : le contrôle porte sur les jours
          qu’il contient, jusqu’à aujourd’hui. Plusieurs fichiers peuvent être importés d’un coup.
        </p>
      )}

      {dfc && data?.centre_sans_fichier && (
        <p className="panneau-description">
          Aucun fichier de la DFC n’est importé pour le centre {data.centre}. Centres importés : {dfc.centres.join(', ')}.
        </p>
      )}

      {dfc && t && !data.centre_sans_fichier && (
        <div className={chargement ? 'en-chargement' : ''}>
          {/* 1. Toute la période des fichiers importés : écarts et tableau par mois. */}
          <div className="sous-entete">
            <h3>
              Par mois, du {fmtDate(data.couverture.debut)} au {fmtDate(data.couverture.fin)}
            </h3>
            <span className="exports">
              <a className="btn btn-petit" href={urlExportDfc(filtres, 'xlsx', undefined, 'mois')} download>
                Excel
              </a>
              <a className="btn btn-petit" href={urlExportDfc(filtres, 'csv', undefined, 'mois')} download>
                CSV
              </a>
            </span>
          </div>
          <section className="tuiles tuiles-ecart" aria-label="Écarts sur toute la période importée">
            {carteEcart('Écart versé (CRM − DFC)', t.verse_dfc, t.verse_crm, t.ecart_verse, t.pct_ecart_verse)}
            <div className={`tuile${t.nb_non_saisis > 0 ? ' tuile-ecart' : ''}`}>
              <div className="tuile-libelle">Jours non saisis par la DFC</div>
              <div className="tuile-valeur">
                {fmtEntier(t.nb_non_saisis)}{' '}
                <span className="tuile-unite">({fmtPct(t.nb_jours ? (100 * t.nb_non_saisis) / t.nb_jours : null)})</span>
              </div>
              <div className="tuile-detail">
                {fmtMontant(t.encaisse_non_saisi)} MRU encaissés et {fmtMontant(t.verse_non_saisi)} versés au CRM ces
                jours-là
              </div>
            </div>
          </section>
          <p className="carte-note">
            Écart = CRM − DFC : ce que le CRM dit versé et dont la DFC n’a pas les pièces. Un jour que la DFC n’a pas
            saisi compte pour zéro de son côté. Cliquer sur un mois pour voir ses jours.
          </p>
          <DataTable
            colonnes={COLONNES.dfc_mois}
            lignes={data.mois ?? []}
            recherche={recherche}
            onLigne={onPeriode ? (m) => onPeriode(m.mois, m.centre) : undefined}
            aideLigne="Cliquer pour voir les jours de ce mois"
          />

          {/* 2. La période choisie en haut de la page : détail par jour. */}
          <div className="sous-entete">
            <h3>
              Par jour, du {fmtDate(data.periode.debut)} au {fmtDate(data.periode.fin)}
            </h3>
          </div>
          {(data.lignes ?? []).length === 0 ? (
            <p className="panneau-description">
              Aucun jour sur la période choisie en haut de la page. Cliquez sur un mois du tableau ci-dessus pour voir
              ses jours.
            </p>
          ) : (
            <>
              <div className="tuiles tuiles-resultat">
                {data.synthese.map((s) => (
                  <button
                    type="button"
                    key={s.code}
                    className={`tuile tuile-bouton${filtre === s.code ? ' actif' : ''}`}
                    aria-pressed={filtre === s.code}
                    onClick={() => setFiltre(s.code)}
                  >
                    <div className="tuile-libelle">
                      <StatusBadge code={s.code} libelle={s.libelle} />
                    </div>
                    <div className="tuile-valeur">
                      {fmtEntier(s.nb)} <span className="tuile-unite">({fmtPct(s.pct_nb)})</span>
                    </div>
                    <div className="tuile-detail">jours</div>
                  </button>
                ))}
              </div>

              <section className="liste-tuile" aria-label="Liste de la case choisie">
                <div className="sous-entete">
                  <h3>
                    {caseOuverte
                      ? `${caseOuverte.libelle} : ${fmtEntier(caseOuverte.nb)} jours (${fmtPct(caseOuverte.pct_nb)})`
                      : filtre === 'ECARTS'
                        ? `Jours avec un écart : ${fmtEntier(lignes.length)} (${fmtPct(
                            data.lignes.length ? (100 * lignes.length) / data.lignes.length : null
                          )})`
                        : `Tous les jours : ${fmtEntier(lignes.length)}`}
                  </h3>
                  <span className="exports">
                    {filtre !== 'ECARTS' && (
                      <button type="button" className="btn btn-petit" onClick={() => setFiltre('ECARTS')}>
                        Voir les écarts
                      </button>
                    )}
                    {filtre !== 'TOUS' && (
                      <button type="button" className="btn btn-petit" onClick={() => setFiltre('TOUS')}>
                        Voir tous les jours
                      </button>
                    )}
                    <a
                      className="btn btn-petit"
                      href={urlExportDfc(filtres, 'xlsx', caseOuverte ? filtre : undefined, 'jours')}
                      download
                    >
                      Excel
                    </a>
                    <a
                      className="btn btn-petit"
                      href={urlExportDfc(filtres, 'csv', caseOuverte ? filtre : undefined, 'jours')}
                      download
                    >
                      CSV
                    </a>
                  </span>
                </div>
                <p className="panneau-description">
                  {EXPLICATIONS[filtre] ??
                    'Tous les jours où la DFC et le CRM ne disent pas la même chose, jours non saisis compris.'}
                </p>
                <DataTable
                  colonnes={COLONNES.dfc_lignes}
                  lignes={lignes}
                  recherche={recherche}
                  chargement={chargement}
                  onLigne={onCentre && !filtres.centre ? (l) => onCentre(l.centre) : undefined}
                  aideLigne="Cliquer pour ne garder que ce centre"
                />
              </section>
            </>
          )}
        </div>
      )}
    </div>
  );
}
