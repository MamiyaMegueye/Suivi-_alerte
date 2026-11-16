const nfMontant = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 0, maximumFractionDigits: 2 });
const nfEntier = new Intl.NumberFormat('fr-FR', { maximumFractionDigits: 0 });
const nfPct = new Intl.NumberFormat('fr-FR', { minimumFractionDigits: 1, maximumFractionDigits: 1 });

export const estVide = (v) => v === null || v === undefined || v === '';

export const fmtMontant = (v) => (estVide(v) ? '—' : nfMontant.format(v));
export const fmtEntier = (v) => (estVide(v) ? '—' : nfEntier.format(v));
export const fmtPct = (v) => (estVide(v) ? '—' : `${nfPct.format(v)} %`);

export function fmtDate(iso) {
  if (estVide(iso)) return '—';
  const s = String(iso);
  return s.length >= 10 ? `${s.slice(8, 10)}/${s.slice(5, 7)}/${s.slice(0, 4)}` : s;
}

export function fmtHeure(iso) {
  if (estVide(iso)) return '—';
  const s = String(iso);
  return s.length >= 19 ? s.slice(11, 19) : s;
}

export function versISO(d) {
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

export function decaler(d, jours) {
  const r = new Date(d);
  r.setDate(r.getDate() + jours);
  return r;
}

/** Périodes proposées dans la barre de filtres. */
export function periodes() {
  const auj = new Date();
  const hier = decaler(auj, -1);
  const debutMois = new Date(auj.getFullYear(), auj.getMonth(), 1);
  const debutMoisPrec = new Date(auj.getFullYear(), auj.getMonth() - 1, 1);
  const finMoisPrec = new Date(auj.getFullYear(), auj.getMonth(), 0);
  return [
    { id: 'auj', libelle: "Aujourd'hui", debut: versISO(auj), fin: versISO(auj) },
    { id: 'hier', libelle: 'Hier', debut: versISO(hier), fin: versISO(hier) },
    { id: '7j', libelle: '7 jours', debut: versISO(decaler(auj, -6)), fin: versISO(auj) },
    { id: 'mois', libelle: 'Mois en cours', debut: versISO(debutMois), fin: versISO(auj) },
    { id: 'moisprec', libelle: 'Mois précédent', debut: versISO(debutMoisPrec), fin: versISO(finMoisPrec) },
  ];
}

/** « 1 centre », « 3 centres ». */
export function pluriel(n, mot) {
  return `${fmtEntier(n)} ${mot}${Math.abs(Number(n) || 0) > 1 ? 's' : ''}`;
}
