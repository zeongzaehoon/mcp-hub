import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/Button";

interface LoadMoreProps {
  loaded: number;
  hasNextPage: boolean;
  isFetchingNextPage: boolean;
  onLoadMore: () => void;
}

/** knitlog table footer: "총 N개 결과" left, paging right. Our lists are cursor-paged (append), so there are no page
 * numbers: 이전 is never available and 다음 appends the next page. */
export function LoadMore({ loaded, hasNextPage, isFetchingNextPage, onLoadMore }: LoadMoreProps) {
  return (
    <div className="flex items-center justify-between text-[13px] text-muted-foreground">
      <span>
        총 <strong className="font-semibold text-foreground">{loaded.toLocaleString()}</strong>개 결과
        {hasNextPage ? <span className="text-subtle"> (더 있음)</span> : null}
      </span>
      <span className="inline-flex items-center gap-1">
        <Button variant="outline" size="sm" disabled aria-label="이전 페이지">
          <ChevronLeft className="h-3.5 w-3.5" /> 이전
        </Button>
        <Button variant="outline" size="sm" onClick={onLoadMore} loading={isFetchingNextPage} disabled={!hasNextPage} aria-label="다음 페이지">
          다음 <ChevronRight className="h-3.5 w-3.5" />
        </Button>
      </span>
    </div>
  );
}
