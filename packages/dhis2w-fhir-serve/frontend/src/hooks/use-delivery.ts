import { useEffect, useState } from 'react'

import { readDelivery } from '@/lib/api'
import type { DeliveryStatus } from '@/lib/spool'

/** How often the status is read again, because a worker on the server moves receipts with nothing happening here. */
const DELIVERY_REFRESH_MILLISECONDS = 30_000

/**
 * What this server has not landed in DHIS2, read again while the screen stays open.
 *
 * A read that fails leaves `status` null and the notice unsaid: `useSpool` reports that server
 * trouble on the same screens, and two errors about one outage would say nothing the first did not.
 */
export function useDelivery(): { status: DeliveryStatus | null } {
    const [status, setStatus] = useState<DeliveryStatus | null>(null)
    useEffect(() => {
        let cancelled = false
        const read = () => {
            readDelivery()
                .then((answer) => {
                    if (!cancelled) setStatus(answer)
                })
                .catch(() => {
                    if (!cancelled) setStatus(null)
                })
        }
        read()
        const timer = setInterval(read, DELIVERY_REFRESH_MILLISECONDS)
        return () => {
            cancelled = true
            clearInterval(timer)
        }
    }, [])
    return { status }
}
