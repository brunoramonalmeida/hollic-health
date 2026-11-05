import { useEffect, useImperativeHandle, useRef, useState, forwardRef } from 'react'

/**
 * Hand-drawn signature on a canvas (mouse, pen or touch). `ref.toJpeg()`
 * returns the drawing as a JPEG data URL on a white background (what the
 * API stores and prints on the receipt), or null when nothing was drawn.
 *
 * Pointer events are attached natively to the canvas so pointer capture
 * works the same on every device. Never render the pad inside a <label>: a
 * label forwards clicks to its first button — the "Clear" button here.
 */
const SignaturePad = forwardRef(function SignaturePad({ width = 420, height = 160, onChange }, ref) {
  const canvas = useRef(null)
  const drawing = useRef(false)
  const strokesRef = useRef(0)
  const [strokes, setStrokes] = useState(0)

  function reset() {
    const ctx = canvas.current.getContext('2d')
    ctx.fillStyle = '#ffffff'
    ctx.fillRect(0, 0, width, height)
    strokesRef.current = 0
    setStrokes(0)
    onChange?.(false)
  }

  useEffect(() => {
    const element = canvas.current
    reset()

    function context() {
      const ctx = element.getContext('2d')
      ctx.lineWidth = 2.2
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'
      ctx.strokeStyle = '#16324f'
      return ctx
    }

    function point(event) {
      const rect = element.getBoundingClientRect()
      return { x: ((event.clientX - rect.left) * width) / rect.width, y: ((event.clientY - rect.top) * height) / rect.height }
    }

    function down(event) {
      event.preventDefault()
      drawing.current = true
      try {
        element.setPointerCapture(event.pointerId)
      } catch {
        /* capture is an optimisation only */
      }
      const { x, y } = point(event)
      const ctx = context()
      ctx.beginPath()
      ctx.moveTo(x, y)
      ctx.lineTo(x + 0.1, y + 0.1)
      ctx.stroke()
    }

    function move(event) {
      if (!drawing.current) return
      event.preventDefault()
      const { x, y } = point(event)
      const ctx = context()
      ctx.lineTo(x, y)
      ctx.stroke()
    }

    function up(event) {
      if (!drawing.current) return
      event.preventDefault()
      drawing.current = false
      strokesRef.current += 1
      setStrokes(strokesRef.current)
      onChange?.(true)
    }

    element.addEventListener('pointerdown', down)
    element.addEventListener('pointermove', move)
    element.addEventListener('pointerup', up)
    element.addEventListener('pointercancel', up)
    element.addEventListener('pointerleave', up)
    return () => {
      element.removeEventListener('pointerdown', down)
      element.removeEventListener('pointermove', move)
      element.removeEventListener('pointerup', up)
      element.removeEventListener('pointercancel', up)
      element.removeEventListener('pointerleave', up)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useImperativeHandle(ref, () => ({
    toJpeg: () => (strokesRef.current > 0 ? canvas.current.toDataURL('image/jpeg', 0.85) : null),
    clear: reset,
    isEmpty: () => strokesRef.current === 0,
  }))

  return (
    <div className="signature-pad" data-testid="signature-pad">
      <canvas ref={canvas} width={width} height={height} role="img" aria-label="Signature pad: draw your signature" />
      <div className="signature-pad-foot">
        <small className="muted" data-testid="signature-pad-state">{strokes ? 'Signature drawn' : 'Draw your signature above'}</small>
        <button type="button" className="ghost" onClick={reset}>Clear</button>
      </div>
    </div>
  )
})

export default SignaturePad
