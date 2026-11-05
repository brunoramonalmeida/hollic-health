import { useEffect, useState } from 'react'
import { useParams } from 'react-router-dom'
import CompanyStatement from '../../components/CompanyStatement'
import PageHeader from '../../components/PageHeader'
import { api } from '../../api'

/** The staff's view of a company's statement (the one its portal shows). */
export default function PartnerStatement() {
  const { id } = useParams()
  const [partner, setPartner] = useState(null)
  const [agreements, setAgreements] = useState([])
  const [error, setError] = useState('')

  useEffect(() => {
    Promise.all([api(`/partners/${id}`), api(`/agreements?partner_id=${id}`)])
      .then(([row, terms]) => {
        setPartner(row)
        setAgreements(terms)
      })
      .catch((err) => setError(err.message))
  }, [id])

  return (
    <section>
      <PageHeader
        back={{ to: `/partners/${id}`, label: partner ? partner.name : 'Partner' }}
        title={`Statement · ${partner ? partner.name : ''}`}
        hint="What the company pays and what its employees pay, by month and by employee — the statement its portal shows."
      />
      {error && <p className="error">{error}</p>}
      <CompanyStatement endpoint={`/partners/${id}/statement`} agreements={agreements} onError={setError} />
    </section>
  )
}
