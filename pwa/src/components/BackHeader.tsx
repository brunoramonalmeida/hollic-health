import { useNavigate } from 'react-router-dom'

interface Props {
  title: string
  onBack?: () => void
}

export function BackHeader({ title, onBack }: Props) {
  const navigate = useNavigate()
  return (
    <div className="back-header">
      <button type="button" onClick={onBack ?? (() => navigate(-1))} aria-label="Back">
        <img src="/assets/icons/arrow-back.svg" alt="" />
      </button>
      <h1>{title}</h1>
    </div>
  )
}
