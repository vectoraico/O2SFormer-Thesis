import torch
import torch.nn.functional as F
from torch import Tensor
from mmdet.core.bbox.match_costs.builder import MATCH_COST
from mmdet.core.bbox.match_costs import FocalLossCost

@MATCH_COST.register_module()
class Distance_cost:
    def __init__(self,weight):
        self.weight = weight
        
    def point_distance(self,predictions: Tensor,targets: Tensor,img_w):
        """
        Args:
            prediction: [num_query, dim]
            targets: [num_targets, dim]
        """

        num_query = predictions.shape[0]
        num_targets = targets.shape[0]

        predictions = torch.repeat_interleave(predictions[:,6:],num_targets,dim=0)
        targets = torch.cat([targets]*num_query,dim=0)[:,6:]

        invalid_masks = (targets < 0) | (targets >= img_w)
        lengths = (~invalid_masks).sum(dim=-1)
        distances = torch.abs((targets - predictions))
        distances[invalid_masks] = 0.
        distances = distances.sum(dim=-1) / (lengths.float() + 1e-9)
        distances = distances.view(num_query,num_targets)

        return distances
    
    def __call__(self,
                 predictions: Tensor,
                 targets: Tensor,
                 img_w,
                 img_h,):
        num_query = predictions.size(0)
        num_gts = targets.size(0)

        distances_score = self.point_distance(predictions,targets,img_w)
        distances_score = 1 - (distances_score / torch.max(distances_score)
                           ) + 1e-2  # normalize the distance
        
        target_start_xys = targets[:, 2:4]  # num_targets, 2
        target_start_xys[..., 0] *= (img_h - 1)
        prediction_start_xys = predictions[:, 2:4]
        prediction_start_xys[..., 0] *= (img_h - 1)

        start_xys_score = torch.cdist(prediction_start_xys, target_start_xys,p=2).reshape(num_query, num_gts)
        start_xys_score = (1 - start_xys_score / torch.max(start_xys_score)) + 1e-2

        target_thetas = targets[..., 4].unsqueeze(-1)
        prediction_thetas = predictions[..., 4].unsqueeze(-1)

        theta_score = torch.cdist(prediction_thetas,target_thetas,p=1).reshape(num_query, num_gts) * 180
        theta_score = (1 - theta_score / torch.max(theta_score)) + 1e-2

        distance_cost = -(distances_score * start_xys_score * theta_score)**2*self.weight

        return distance_cost

@MATCH_COST.register_module()
class FocalIOULossCost(FocalLossCost):
    def _focal_loss_cost(self, cls_pred:Tensor, gt_labels:Tensor,pair_wise_iou):
        """
        Args:
            cls_pred (Tensor): Predicted classification logits, shape
                (num_query, num_class).
            gt_labels (Tensor): Label of `gt_bboxes`, shape (num_gt,).
        Returns:
            torch.Tensor: cls_cost value with weight
        """
        cls_pred = cls_pred.sigmoid()
        pair_wise_iou = F.sigmoid(pair_wise_iou.clone())
        num_query = cls_pred.shape[0]
        num_gt = gt_labels.shape[0]
        gt_onehot_label = (
            F.one_hot(gt_labels.to(torch.int64),cls_pred.shape[-1]).float().unsqueeze(0).repeat(num_query, 1, 1))    
        valid_pred_scores = cls_pred.unsqueeze(1).repeat(1, num_gt, 1)    
        soft_label = gt_onehot_label * pair_wise_iou[..., None]
        scale_factor = soft_label - valid_pred_scores
        soft_cls_cost = F.binary_cross_entropy_with_logits(
            valid_pred_scores, soft_label,
            reduction='none') * scale_factor.abs().pow(2.0)
        soft_cls_cost = soft_cls_cost.sum(dim=-1)
        return soft_cls_cost * self.weight

    def __call__(self, cls_pred, gt_labels,pair_wise_iou=None):
        """
        Args:
            cls_pred (Tensor): Predicted classfication logits.
            gt_labels (Tensor)): Labels.
        Returns:
            Tensor: Focal cost matrix with weight in shape\
                (num_query, num_gt).
        """
        if self.binary_input:
            return self._mask_focal_loss_cost(cls_pred, gt_labels)
        else:
            assert pair_wise_iou is not None,'pair_wise_iou should not be None'
            return self._focal_loss_cost(cls_pred, gt_labels,pair_wise_iou)    