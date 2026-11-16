import { useMemo, useState } from 'react';
import { importerReleve, supprimerReleve, urlExportReleve } from '../api.js';
import { COLONNES } from '../datasets.js';
import { fmtDate, fmtEntier, fmtMontant, fmtPct } from '../format.js';
import { useData } from '../hooks/useData.js';
import DataTable from './DataTable.jsx';
import StatusBadge from './StatusBadge.jsx';

/** Boutons d'export d'un seul tableau de l'onglet. */
function Exports({ filtres, partie, resultat }) {
  return (
    <span className="exports">
      <a className="btn btn-petit" href={urlExportReleve(filtres, partie, 'xlsx', resultat)} download>
        Excel
      </a>
      <a className="btn btn-petit" href={urlExportReleve(filtres, partie, 'csv', resultat)} download>
        CSV
      </a>
    </span>
  );
}

/**
 * Écart global de la période : ce que le CRM dit versé, face à ce que la
 * banque a reçu. Pour un centre, le relevé n'indique pas le centre : la banque
 * est alors la part retrouvée de ses versements.
 */
function EcartGlobal({ g }) {
  if (!g) return null;
  const parReleve = g.base === 'releve';
  const signe = g.ecart > 0 ? '+' : '';
  return (
    <section className="tuiles tuiles-ecart" aria-label="Écart global de la période">
      <div className="tuile">
        <div className="tuile-libelle">Versé selon le CRM</div>
        <div className="tuile-valeur">
          {fmtMontant(g.crm)} <span className="tuile-unite">MRU</span>
        </div>
        <div className="tuile-detail">
          {fmtEntier(g.nb_crm)} versements validés · {fmtEntier(g.nb_retrouves)} retrouvés (
          {fmtPct(g.nb_crm ? (100 * g.nb_retrouves) / g.nb_crm : null)})
        </div>
      </div>
      <div className="tuile">
        <div className="tuile-libelle">{parReleve ? 'Reçu par la banque (relevé)' : 'Retrouvé à la banque'}</div>
        <div className="tuile-valeur">
          {fmtMontant(g.banque)} <span className="tuile-unite">MRU</span>
        </div>
        <div className="tuile-detail">
          {parReleve
            ? `${fmtEntier(g.nb_depots)} dépôts d’espèces (${fmtMontant(g.depots)})${
                g.nb_annules ? ` − ${fmtEntier(g.nb_annules)} annulés par la banque (${fmtMontant(g.annules)})` : ''
              }`
            : 'Le relevé n’indique pas le centre : part retrouvée de ses versements'}
        </div>
      </div>
      <div className={`tuile${Math.abs(g.ecart) >= 1 ? ' tuile-ecart' : ''}`}>
        <div className="tuile-libelle">Écart global (CRM − banque)</div>
        <div className="tuile-valeur">
          {signe}
          {fmtMontant(g.ecart)} <span className="tuile-unite">MRU</span>
        </div>
        <div className="tuile-detail">
          {fmtPct(g.pct_ecart)} du montant du CRM ·{' '}
          {g.ecart >= 1 ? 'le CRM déclare plus que la banque' : g.ecart <= -1 ? 'la banque a reçu plus que le CRM' : 'aucun écart'}
        </div>
      </div>
    </section>
  );
}

// Ce que signifie chaque case, affiché au-dessus de sa liste.
const EXPLICATIONS = {
  MEME_JOUR: 'Le dépôt est à la banque déclarée, pour le même montant, le jour même du versement.',
  DECALE: 'Le dépôt est à la banque déclarée, pour le même montant, à quelques jours du versement (3 jours avant à 10 jours après).',
  GROUPE: 'Plusieurs versements du même centre ont été déposés en une seule fois : leur somme égale un dépôt du relevé.',
  FRACTIONNE: 'Le versement a été déposé en plusieurs fois : la somme de plusieurs dépôts du relevé égale son montant.',
  ARRONDI: 'Le dépôt est à la banque déclarée, avec un écart de montant de 10 au plus (arrondi).',
  TRES_DECALE: 'Le dépôt est à la banque déclarée, pour le même montant, mais à plus de 10 jours du versement. À vérifier : l’argent est resté longtemps hors de la banque, ou le versement a été saisi après coup.',
  AUTRE_BANQUE: 'Un dépôt du même montant existe à une date proche, mais dans une autre banque que celle déclarée dans le CRM. À vérifier.',
  MONTANT_DIFFERENT: 'Un dépôt existe à la banque déclarée à une date proche, mais avec un écart de montant (500 au plus). À vérifier.',
  NON_RETROUVE: 'Versements validés dans le CRM pour lesquels aucun dépôt correspondant n’a été trouvé à la banque. Ce sont les cas à contrôler en priorité, classés du plus gros montant au plus petit.',
  TOUS: 'Tous les versements validés du CRM de la période, avec le résultat de leur recherche dans le relevé.',
};

/**
 * Onglet « Rapprochement bancaire » : import d'un relevé bancaire, puis
 * croisement avec les versements validés du CRM sur la période affichée.
 */
export default function Releve({ filtres, version, recherche, onCentre }) {
  const [imports, setImports] = useState(0);
  const [envoi, setEnvoi] = useState({ enCours: false, erreur: null });
  // Case ouverte : sa liste s'affiche juste sous les cases. Par défaut, les non retrouvés.
  const [filtre, setFiltre] = useState('NON_RETROUVE');
  const { data, chargement, erreur, attente } = useData('releve/rapprochement', filtres, version + imports);

  // Les relevés s'ajoutent les uns aux autres : un versement de fin de mois est
  // souvent déposé au début du mois suivant. Plusieurs fichiers peuvent être
  // choisis d'un coup ; ils sont envoyés l'un après l'autre.
  const [ajouts, setAjouts] = useState([]);
  const importer = async (fichiers) => {
    setEnvoi({ enCours: true, erreur: null });
    const faits = [];
    try {
      for (const fichier of fichiers) {
        const reponse = await importerReleve(fichier);
        if (reponse?.releve?.ajout) faits.push(reponse.releve.ajout);
      }
      setEnvoi({ enCours: false, erreur: null });
    } catch (err) {
      setEnvoi({ enCours: false, erreur: err.message });
    }
    setAjouts(faits);
    if (faits.length) {
      setFiltre('NON_RETROUVE');
      setImports((n) => n + 1);
    }
  };

  // Réinitialiser : efface le relevé importé, après confirmation.
  const [confirmation, setConfirmation] = useState(false);
  const reinitialiser = async () => {
    setConfirmation(false);
    setEnvoi({ enCours: true, erreur: null });
    try {
      await supprimerReleve();
      setAjouts([]);
      setEnvoi({ enCours: false, erreur: null });
      setFiltre('NON_RETROUVE');
      setImports((n) => n + 1);
    } catch (err) {
      setEnvoi({ enCours: false, erreur: err.message });
    }
  };

  const releve = data?.releve ?? null;
  const lignes = useMemo(() => {
    const toutes = data?.lignes ?? [];
    if (filtre === 'TOUS') return toutes;
    const choisies = toutes.filter((l) => l.resultat_code === filtre);
    // Les non retrouvés sont classés du plus gros montant au plus petit.
    return filtre === 'NON_RETROUVE' ? [...choisies].sort((x, y) => y.montant - x.montant) : choisies;
  }, [data, filtre]);
  const caseOuverte = data?.synthese?.find((c) => c.code === filtre);

  const boutonImport = (
    <label className="btn btn-fichier">
      {envoi.enCours ? 'Lecture du relevé…' : releve ? 'Ajouter un relevé' : 'Importer un relevé bancaire (Excel)'}
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
  );
  const nbFichiers = releve?.fichiers?.length ?? 0;

  return (
    <div className="comparaison">
      <div className="comparaison-outils">
        {boutonImport}
        {releve && (
          <span className="fichier-nom" title={releve.nom_fichier}>
            {nbFichiers > 1 ? `${nbFichiers} relevés` : releve.nom_fichier} · {fmtEntier(releve.nb_lignes)} lignes, dont{' '}
            {fmtEntier(releve.nb_versements)} versements d’espèces · du {fmtDate(releve.date_min)} au{' '}
            {fmtDate(releve.date_max)}
          </span>
        )}
        {releve && data && (
          <a className="btn btn-principal" href={urlExportReleve(filtres)} download>
            Exporter tout l’onglet (Excel)
          </a>
        )}
        {releve && !confirmation && (
          <button type="button" className="btn" disabled={envoi.enCours} onClick={() => setConfirmation(true)}>
            Réinitialiser
          </button>
        )}
        {releve && confirmation && (
          <span className="confirmation" role="group" aria-label="Confirmer la réinitialisation">
            {nbFichiers > 1 ? `Effacer les ${nbFichiers} relevés importés ?` : 'Effacer le relevé importé ?'}
            <button type="button" className="btn btn-alerte" onClick={reinitialiser}>
              Oui, effacer
            </button>
            <button type="button" className="btn" onClick={() => setConfirmation(false)}>
              Annuler
            </button>
          </span>
        )}
      </div>

      {envoi.erreur && (
        <p className="alerte" role="alert">
          {envoi.erreur}
        </p>
      )}
      {erreur && !data && <p className="alerte">{erreur}</p>}
      {ajouts.map((a) => (
        <p className="info-chargement" role="status" key={a.nom_fichier}>
          {a.nom_fichier} : {fmtEntier(a.nb_ajoutees)} {a.nb_ajoutees > 1 ? 'lignes ajoutées' : 'ligne ajoutée'}
          {a.nb_deja_presentes > 0 && ` · ${fmtEntier(a.nb_deja_presentes)} déjà présentes, non reprises`}
        </p>
      ))}
      {releve?.trous?.length > 0 && (
        <p className="alerte" role="alert">
          Aucune ligne de relevé {releve.trous.map((t) => `du ${fmtDate(t.debut)} au ${fmtDate(t.fin)}`).join(', ')} :
          il manque un relevé. Les versements de ces jours ne peuvent pas être retrouvés.
        </p>
      )}
      {attente && (
        <p className="info-chargement" role="status">
          {attente}
        </p>
      )}

      {data && !releve && !envoi.enCours && (
        <p className="panneau-description">
          Aucun relevé importé. Le fichier attendu est le relevé des banques au format Excel, avec les colonnes Banque,
          Date, Libellé, Debit et Credit. Une fois importé, il reste enregistré. Plusieurs
          relevés peuvent être importés : ils s’ajoutent, ce qui permet de retrouver un versement de fin de mois
          déposé le mois suivant.
        </p>
      )}

      {releve && data && (
        <div className={chargement ? 'en-chargement' : ''}>
          {releve.banques_inconnues?.length > 0 && (
            <p className="carte-note">
              Banques du relevé sans équivalent connu dans le CRM : {releve.banques_inconnues.join(', ')}.
            </p>
          )}

          <EcartGlobal g={data.global} />

          <div className="sous-entete">
            <h3>Résultats du rapprochement</h3>
            <Exports filtres={filtres} partie="synthese" />
          </div>
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
                <div className="tuile-detail">
                  {fmtMontant(s.montant)} MRU ({fmtPct(s.pct_montant)})
                </div>
              </button>
            ))}
          </div>
          <p className="carte-note">
            Cliquer sur une case pour afficher sa liste juste en dessous. Le relevé ne contient ni le centre ni le
            numéro de versement : le croisement repose sur la banque, le montant et la date.
          </p>

          <section className="liste-tuile" aria-label="Liste de la case choisie">
            <div className="sous-entete">
              <h3>
                {caseOuverte
                  ? `${caseOuverte.libelle} : ${fmtEntier(caseOuverte.nb)} versements (${fmtPct(
                      caseOuverte.pct_nb
                    )} des versements, ${fmtPct(caseOuverte.pct_montant)} du montant)`
                  : `Tous les versements du CRM : ${fmtEntier(lignes.length)}`}
              </h3>
              <span className="exports">
                {filtre !== 'TOUS' && (
                  <button type="button" className="btn btn-petit" onClick={() => setFiltre('TOUS')}>
                    Voir tous les versements
                  </button>
                )}
                <Exports filtres={filtres} partie="lignes" resultat={filtre === 'TOUS' ? undefined : filtre} />
              </span>
            </div>
            <p className="panneau-description">{EXPLICATIONS[filtre]}</p>
            <DataTable
              colonnes={filtre === 'NON_RETROUVE' ? COLONNES.releve_absents : COLONNES.releve_lignes}
              lignes={lignes}
              recherche={recherche}
              chargement={chargement}
              onLigne={onCentre ? (l) => onCentre(l.centre) : undefined}
              aideLigne="Cliquer pour ne garder que ce centre"
            />
          </section>

          <div className="sous-entete">
            <h3>Par banque</h3>
            <Exports filtres={filtres} partie="banques" />
          </div>
          <DataTable colonnes={COLONNES.releve_banques} lignes={data.banques} recherche={recherche} />

          <div className="sous-entete">
            <h3>Par centre</h3>
            <Exports filtres={filtres} partie="centres" />
          </div>
          <DataTable
            colonnes={COLONNES.releve_centres}
            lignes={data.centres}
            recherche={recherche}
            onLigne={onCentre ? (l) => onCentre(l.centre) : undefined}
            aideLigne="Cliquer pour ne garder que ce centre"
          />

          {data.centre ? (
            <p className="carte-note">
              Les dépôts du relevé sans versement dans le CRM et les versements annulés par la banque ne sont listés
              que pour « Tous les centres » : le relevé n’indique pas le centre.
            </p>
          ) : (
            <>
              <div className="sous-entete">
                <h3>Dépôts d’espèces du relevé sans versement dans le CRM</h3>
                <Exports filtres={filtres} partie="sans_crm" />
              </div>
              <p className="panneau-description">
                Dépôts reçus par la banque sur la période et qui ne correspondent à aucun versement validé du CRM :
                paiement d’un client à la banque, ou versement non saisi.
              </p>
              <DataTable colonnes={COLONNES.releve_sans_crm} lignes={data.sans_crm} recherche={recherche} />

              <div className="sous-entete">
                <h3>Versements annulés par la banque</h3>
                <Exports filtres={filtres} partie="annules" />
              </div>
              <p className="panneau-description">
                Versements d’espèces passés au débit du compte : la banque les a retirés après les avoir enregistrés.
              </p>
              <DataTable colonnes={COLONNES.releve_annules} lignes={data.annules} recherche={recherche} />
            </>
          )}
        </div>
      )}
    </div>
  );
}
