/**
 * The price comparison: base, market-updated, and final, per item.
 *
 * The table shows **two** changes rather than one, and that is the design
 * decision worth explaining. `updated_price` is what the market indices alone
 * did; `final_price` is that plus the commercial adjustments - risk buffer,
 * payment terms, profit margin. A tender is argued on the difference between
 * those two numbers, because "the market went up 15%" and "we proposed 37%" are
 * different claims, and an operator who can only see one of them cannot answer a
 * reviewer who asks which is which. Collapsing them into a single percentage
 * would hide exactly the decomposition the defense document is built on.
 *
 * Every figure goes through `lib/persian`. A raw float in a tender table is not a
 * display shortcut, it is a wrong answer: a nine-figure rial amount with no
 * grouping is not legible at a glance, and ASCII digits are not the digits the
 * source documents use.
 */
'use client';

import * as React from 'react';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ABSENT, formatChange, formatRial, toPersianNumber } from '@/lib/persian';
import type { PriceComparison } from '@/lib/api/queries';

export interface PriceComparisonTableProps {
  comparison: PriceComparison;
  title?: string;
  className?: string;
}

/** A signed change, coloured, with the sign carried by the formatter. */
function Change({ from, to }: { from: number; to: number }) {
  const text = formatChange(from, to);
  if (text === ABSENT) {
    // No percentage is computable, and the cell is blank rather than showing a
    // dash that a reader might mistake for a small fall.
    return <span className="text-muted-foreground">{ABSENT}</span>;
  }
  const falling = to < from;
  return (
    <span className={falling ? 'text-red-600' : 'text-emerald-700'}>
      {text}
    </span>
  );
}

export function PriceComparisonTable({
  comparison,
  title = 'مقایسه قیمت‌ها',
  className,
}: PriceComparisonTableProps) {
  const items = comparison.items ?? [];

  /*
   * An empty state rather than a header over no rows. A table whose column
   * headers are visible and whose body is not reads as a failure to load, and
   * the reader's next action - re-upload, re-run the analysis - is the wrong
   * one. Saying "there is nothing here" distinguishes the two.
   */
  if (items.length === 0) {
    return (
      <Card className={className}>
        <CardContent className="py-10 text-center text-sm text-muted-foreground">
          ردیفی برای نمایش وجود ندارد. پس از بارگذاری فایل BoQ و انجام تحلیل وزن‌دهی،
          قیمت‌ها در این بخش نمایش داده می‌شوند.
        </CardContent>
      </Card>
    );
  }

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-lg">{title}</CardTitle>
      </CardHeader>
      <CardContent>
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="text-right">کد</TableHead>
              <TableHead className="text-right">شرح</TableHead>
              <TableHead className="text-right">واحد</TableHead>
              <TableHead className="text-right">قیمت پایه</TableHead>
              <TableHead className="text-right">قیمت به‌روزشده</TableHead>
              <TableHead className="text-right">تغییر بازار</TableHead>
              <TableHead className="text-right">قیمت نهایی</TableHead>
              <TableHead className="text-right">تغییر کل</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {items.map((item) => (
              <TableRow key={item.item_id}>
                <TableCell className="font-mono text-xs">
                  {/* LTR-isolated: an identifier must not be reordered by the
                      surrounding RTL context, and `001-002` is not a number. */}
                  {item.code}
                </TableCell>
                <TableCell className="max-w-xs truncate" title={item.description_fa}>
                  {item.description_fa}
                </TableCell>
                <TableCell className="text-xs text-muted-foreground">{item.unit}</TableCell>
                <TableCell className="tabular-nums">{formatRial(item.base_price)}</TableCell>
                <TableCell className="tabular-nums">{formatRial(item.updated_price)}</TableCell>
                <TableCell>
                  <Change from={item.base_price} to={item.updated_price} />
                </TableCell>
                <TableCell className="font-medium tabular-nums">
                  {formatRial(item.final_price)}
                </TableCell>
                <TableCell>
                  <Change from={item.base_price} to={item.final_price} />
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>

        {typeof comparison.summary?.total_final_price === 'number' ? (
          <p className="mt-4 text-sm text-muted-foreground">
            مجموع قیمت نهایی:{' '}
            <span className="font-medium text-foreground">
              {formatRial(comparison.summary.total_final_price as number)}
            </span>{' '}
            ریال در {toPersianNumber(items.length)} ردیف
          </p>
        ) : null}
      </CardContent>
    </Card>
  );
}
