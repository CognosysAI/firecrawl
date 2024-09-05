import { Request, Response } from "express";
import { Logger } from "../../lib/logger";
import {
  Document,
  legacyDocumentConverter,
  legacyExtractorOptions,
  legacyScrapeOptions,
  RequestWithAuth,
  ScrapeRequest,
  scrapeRequestSchema,
  ScrapeResponse,
} from "./types";
import { v4 as uuidv4 } from "uuid";
import { numTokensFromString } from "../../lib/LLM-extraction/helpers";
import { logJob } from "../../services/logging/log_job";
import { startWebScraperPipeline } from "../../main/runWebScraper";

export async function scrapeController(
  req: RequestWithAuth<{}, ScrapeResponse, ScrapeRequest>,
  res: Response<ScrapeResponse>
) {
  req.body = scrapeRequestSchema.parse(req.body);
  let earlyReturn = false;

  const origin = req.body.origin;
  const timeout = req.body.timeout;
  const pageOptions = legacyScrapeOptions(req.body);
  const extractorOptions = req.body.extract
    ? legacyExtractorOptions(req.body.extract)
    : undefined;
  const jobId = uuidv4();

  const startTime = new Date().getTime();

  try {
    const { success, message, docs } = await startWebScraperPipeline({
      job: {
        id: jobId,
        data: {
          url: req.body.url,
          mode: "single_urls",
          crawlerOptions: {},
          team_id: req.auth.team_id,
          pageOptions,
          extractorOptions,
          origin: req.body.origin,
          is_scrape: true,
        },
        updateProgress: async () => {
          // Do nothing
        },
        opts: {
          priority: 100,
        },
      } as any,
      token: jobId,
    });

    if (!success) {
      throw new Error(message);
    }

    const endTime = new Date().getTime();
    const timeTakenInSeconds = (endTime - startTime) / 1000;

    if (earlyReturn) {
      // Don't bill if we're early returning
      return;
    }

    if (!pageOptions || !pageOptions.includeRawHtml) {
      docs.forEach((doc) => {
        if (doc && doc.rawHtml) {
          delete doc.rawHtml;
        }
      });
    }

    if (pageOptions && pageOptions.includeExtract) {
      if (!pageOptions.includeMarkdown) {
        docs.forEach((doc) => {
          if (doc && doc.markdown) {
            delete doc.markdown;
          }
        });
      }
    }

    // logJob({
    //   job_id: jobId,
    //   success: true,
    //   message: "Scrape completed",
    //   num_docs: docs.length,
    //   docs: docs,
    //   time_taken: timeTakenInSeconds,
    //   team_id: req.auth.team_id,
    //   mode: "scrape",
    //   url: req.body.url,
    //   crawlerOptions: {},
    //   pageOptions: pageOptions,
    //   origin: origin,
    //   extractor_options: { mode: "markdown" },
    //   num_tokens: numTokens,
    // });

    return res.status(200).json({
      success: true,
      data: legacyDocumentConverter(docs[0]),
      scrape_id: origin?.includes("website") ? jobId : undefined,
    });
  } catch (error) {
    Logger.error(`Error in scrapeController: ${error}`);
    return res.status(500).json({
      success: false,
      error: `(Internal server error) - ${
        error && error?.message ? error.message : error
      } ${
        extractorOptions && extractorOptions.mode !== "markdown"
          ? " - Could be due to LLM parsing issues"
          : ""
      }`,
    });
  }
}
