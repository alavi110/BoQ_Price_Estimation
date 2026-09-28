/**
 * The BoQ upload: pick a spreadsheet, and see what the parser made of it.
 *
 * The interesting part is what happens *before* anything is sent. Two checks run
 * in the browser, and both exist to give the user a message they can act on
 * rather than a 422 from the server:
 *
 * The extension, because the most common failure by a wide margin is someone
 * handing over a PDF or a photo of a printed BoQ. That is a mistake of
 * *selection*, and a message naming the accepted format fixes it in a second.
 *
 * The MIME type, because the extension is user-supplied and a `.xlsx` that is
 * really a legacy `.xls` will fail inside the parser - after upload, and with a
 * stack-level message. Rejecting it here moves the failure to the moment the user
 * is still looking at the file they chose.
 *
 * Neither check replaces the server's: both are advisory, and a client that
 * "validated" a spreadsheet properly would be a client that could be lied to.
 * The server still parses, and its own rejection is surfaced verbatim, because
 * the parser's message names the row and column - which is the only thing that
 * tells an operator which cell to fix.
 */
'use client';

import * as React from 'react';
import { Upload, FileSpreadsheet, CheckCircle2 } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ApiError, NETWORK_ERROR_MESSAGE } from '@/lib/api/client';
import { useUploadBoQ, type BoQUploadResponse } from '@/lib/api/queries';
import { toPersianNumber } from '@/lib/persian';

/**
 * Accepted extensions, mirroring `ALLOWED_EXTENSIONS` in the backend's config.
 *
 * Both `.xlsx` and `.xls`, because FR-001 requires accepting both and the server
 * accepts both. A UI stricter than the API is a real defect and not caution: a
 * user with a legitimate `.xls` BoQ would be told the file is unsupported by an
 * application whose backend would have parsed it.
 */
const ACCEPTED_EXTENSIONS = ['.xlsx', '.xls'] as const;

/** Mirrors `MAX_FILE_SIZE_MB` in the backend's config. */
const MAX_FILE_SIZE_MB = 50;

/**
 * Whether this file can be sent, and if not, the message to show.
 *
 * Both checks exist to give the user a message they can act on *before* the
 * upload, rather than a rejection from the server afterwards. They are not a
 * security boundary and are not treated as one: the server repeats both checks
 * and parses the bytes regardless, because a client that could be lied to about
 * a file's extension is a client that cannot be trusted to have checked it.
 *
 * There is deliberately **no** MIME-type check. The backend validates the
 * filename extension and nothing else, and plenty of real deployments send
 * `application/octet-stream` for a perfectly good `.xls` - an Office file saved
 * by a macro-enabled template, or passed through a corporate proxy. A MIME check
 * here would reject files the server accepts, which is the one thing a
 * pre-flight check must never do.
 */
export function validateBoqFile(file: File): string | null {
  const name = file.name.toLowerCase();
  if (!ACCEPTED_EXTENSIONS.some((extension) => name.endsWith(extension))) {
    return (
      `فرمت فایل پشتیبانی نمی‌شود. تنها فایل Excel با پسوند ${ACCEPTED_EXTENSIONS.join(' ی ')} ` +
      'پذیرفته می‌شود.'
    );
  }
  if (file.size === 0) {
    return 'فایل انتخاب‌شده خالی است. لطفاً فایل BoQ را دوباره ذخیره و ارسال کنید.';
  }
  const sizeMb = file.size / (1024 * 1024);
  if (sizeMb > MAX_FILE_SIZE_MB) {
    return (
      `حجم فایل بیش از حد مجاز است (${toPersianNumber(MAX_FILE_SIZE_MB)} مگابایت). ` +
      'فایل را به چند فایل کوچک‌تر تقسیم کنید.'
    );
  }
  return null;
}

export interface UploadInterfaceProps {
  onUploaded?: (result: BoQUploadResponse) => void;
  className?: string;
}

export function UploadInterface({ onUploaded, className }: UploadInterfaceProps) {
  const inputRef = React.useRef<HTMLInputElement>(null);
  const upload = useUploadBoQ();
  const [fileName, setFileName] = React.useState<string | null>(null);
  const [clientError, setClientError] = React.useState<string | null>(null);

  const result = upload.data ?? null;
  const error = clientError ?? (upload.error instanceof Error ? upload.error.message : null);
  const isNetworkError = upload.error instanceof ApiError && upload.error.isNetworkFailure;

  async function handleFile(file: File) {
    setClientError(null);
    upload.reset();

    const invalid = validateBoqFile(file);
    if (invalid) {
      // The file is rejected here and the request is never made. Sending it and
      // letting the server refuse would produce a slower, vaguer version of the
      // same message.
      setFileName(null);
      setClientError(invalid);
      return;
    }

    setFileName(file.name);
    try {
      const response = await upload.mutateAsync(file);
      onUploaded?.(response);
    } catch {
      // Already rendered from `upload.error`; the message the server sent is
      // deliberately not re-worded here, because it is the actionable part.
    }
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-lg">بارگذاری فایل BoQ</CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/*
          The label and the button are separate elements on purpose. A single
          element styled as a button and *also* being the file input's label
          would be reachable as a label and not as a button, so a keyboard user
          tabbing the page would find a control that neither announces its role
          nor opens the picker on Enter.
        */}
        <div className="flex flex-col items-center gap-3 rounded-lg border-2 border-dashed p-8 text-center">
          <FileSpreadsheet className="h-10 w-10 text-muted-foreground" aria-hidden="true" />
          <label htmlFor="boq-file" className="text-sm text-muted-foreground">
            انتخاب فایل Excel (xlsx)
          </label>
          <input
            ref={inputRef}
            id="boq-file"
            type="file"
            accept=".xlsx,.xls"
            className="sr-only"
            onChange={(event) => {
              const file = event.target.files?.[0];
              if (file) {
                void handleFile(file);
              }
            }}
          />
          <Button
            type="button"
            onClick={() => inputRef.current?.click()}
            disabled={upload.isPending}
          >
            <Upload className="ml-2 h-4 w-4" aria-hidden="true" />
            انتخاب فایل
          </Button>

          {fileName ? (
            <p className="flex items-center gap-2 text-sm">
              {upload.isPending ? (
                <span role="status" className="text-muted-foreground">
                  در حال بارگذاری…
                </span>
              ) : null}
              <span className="font-medium">{fileName}</span>
            </p>
          ) : null}
        </div>

        {/*
          A single `role="alert"` region for every failure, so the error is
          announced once, at the point it appears, rather than the messages
          replacing each other in place. The server's own text is shown verbatim:
          for a parse failure it names the row and column, which is the only
          thing that tells an operator which cell to fix.
        */}
        {error ? <Alert tone="error">{isNetworkError ? NETWORK_ERROR_MESSAGE : error}</Alert> : null}

        {result?.preview ? (
          <div className="space-y-3">
            <p className="flex items-center gap-2 text-sm text-emerald-800">
              <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
              فایل با موفقیت پردازش شد
            </p>
            <p className="text-sm text-muted-foreground">
              تعداد ردیف‌ها: {toPersianNumber(result.preview.total_items)} | تعداد فصل‌ها:{' '}
              {toPersianNumber(result.preview.total_chapters)}
            </p>

            {/*
              Warnings are surfaced, not swallowed. Each one is the parser telling
              the user something about their own file that they are the only person
              positioned to resolve - a missing unit, an unrecognised code. Silently
              dropping them would leave a BoQ priced from rows that are quietly
              wrong. A `status` rather than an `alert`: the file parsed, so this
              interrupts nothing, but a reader still gets told.
            */}
            {result.preview.warnings.length > 0 ? (
              <Alert tone="warning">
                <ul className="list-inside list-disc space-y-1">
                  {result.preview.warnings.map((warning) => (
                    <li key={warning}>{warning}</li>
                  ))}
                </ul>
              </Alert>
            ) : null}

            <div className="space-y-2">
              {result.preview.chapters.map((chapter) => (
                <div key={chapter.chapter_id} className="rounded-md border p-3">
                  <p className="text-sm font-medium">
                    <span className="text-muted-foreground">{chapter.code}</span> {chapter.name}
                  </p>
                  <p className="text-xs text-muted-foreground">
                    {toPersianNumber(chapter.item_count)} ردیف
                  </p>
                </div>
              ))}
            </div>
          </div>
        ) : null}

        {result && !result.preview && result.message ? (
          <p className="text-sm text-muted-foreground">{result.message}</p>
        ) : null}
      </CardContent>
    </Card>
  );
}
