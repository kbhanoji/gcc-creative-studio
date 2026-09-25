/**
 * Copyright 2025 Google LLC
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *     http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */


import {Injectable} from '@angular/core';
import {
  HttpErrorResponse,
  HttpEvent,
  HttpHandler,
  HttpInterceptor,
  HttpRequest,
} from '@angular/common/http';
import {Observable, throwError, timer} from 'rxjs';
import {retry} from 'rxjs/operators';

/** ~5 minutes of retries: enough for a stopped Cloud SQL instance to start. */
const MAX_RETRIES = 30;
const RETRY_DELAY_MS = 10000;

/**
 * developlocal: retries requests while the backend is starting a stopped database.
 *
 * The backend answers 503 + `X-DB-Starting: true` before touching the request, so
 * it is safe to retry any method. A 504 from the Firebase Hosting proxy (cold start
 * longer than 60 s) is retried for GET requests only, to never duplicate a generation.
 */
@Injectable()
export class DbStartingInterceptor implements HttpInterceptor {
  intercept(
    request: HttpRequest<unknown>,
    next: HttpHandler,
  ): Observable<HttpEvent<unknown>> {
    return next.handle(request).pipe(
      retry({
        count: MAX_RETRIES,
        delay: (error: unknown, attempt: number) => {
          const starting =
            error instanceof HttpErrorResponse &&
            ((error.status === 503 &&
              error.headers.get('X-DB-Starting') === 'true') ||
              (error.status === 504 && request.method === 'GET'));
          if (!starting) {
            return throwError(() => error);
          }
          console.info(
            `Creative Studio database is starting; retrying ${request.url} (${attempt}/${MAX_RETRIES})`,
          );
          return timer(RETRY_DELAY_MS);
        },
      }),
    );
  }
}
