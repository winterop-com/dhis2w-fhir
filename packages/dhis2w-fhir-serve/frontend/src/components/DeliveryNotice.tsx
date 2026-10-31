import { Link } from 'react-router-dom'

import { Alert, AlertDescription, AlertTitle } from '@/components/ui/alert'
import { useDelivery } from '@/hooks/use-delivery'
import { deliveryNotices, type DeliveryNoticeText } from '@/lib/spool'

/**
 * What has not reached this DHIS2 instance, said where a person will see it.
 *
 * Most refusals reach the submitter in the response. What is left is a capture held while the instance
 * could not be reached, and one refused after its submitter had gone. Nobody reads a directory for
 * those, so the two screens a person opens to check on capture say so unprompted, and say nothing at
 * all while there is nothing to say.
 */
export function DeliveryNotice() {
    const { status } = useDelivery()
    if (status === null) return null
    const notices = deliveryNotices(status)
    if (notices.length === 0) return null
    return (
        <div className="mb-4 space-y-2">
            {notices.map((notice) => (
                <Notice key={notice.lifecycle} notice={notice} />
            ))}
        </div>
    )
}

/** One thing that has not reached the instance, and the receipts it is about. */
function Notice({ notice }: { notice: DeliveryNoticeText }) {
    return (
        <Alert variant={notice.lifecycle === 'rejected' ? 'destructive' : 'default'}>
            <AlertTitle>{notice.title}</AlertTitle>
            <AlertDescription>
                {notice.detail}{' '}
                <Link className="underline" to={`/responses?lifecycle=${notice.lifecycle}`}>
                    Open
                </Link>
            </AlertDescription>
        </Alert>
    )
}
